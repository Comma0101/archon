"""Telegram Bot API adapter (long polling, no extra dependencies)."""

from __future__ import annotations

import datetime as dt
import secrets
import sys
import threading
import time
from types import SimpleNamespace
from typing import Callable, TYPE_CHECKING

from archon.adapters.telegram_approvals import (
    APPROVAL_ACTION_ALLOW15,
    APPROVAL_ACTION_APPROVE,
    APPROVAL_ACTION_DENY,
    ELEVATED_APPROVAL_TTL_SEC,
    PENDING_APPROVAL_TTL_SEC,
    answer_callback_query_safe,
    build_approval_reply_markup,
    build_approval_status_text,
    build_pending_approval_text,
    looks_like_safety_gate_rejection,
    parse_approval_callback_data,
    truncate_approval_command,
)
from archon.adapters.telegram_client import (
    DEFAULT_TELEGRAM_MESSAGE_LIMIT,
    TelegramBotClient,
)
from archon.audio.stt import transcribe_audio_bytes
from archon.audio.tts import convert_wav_to_ogg_opus, synthesize_speech_wav
from archon.cli_repl_commands import (
    _maybe_auto_activate_skill,
    handle_clear_command,
    handle_compact_command,
    handle_context_command,
    handle_cost_command,
    handle_doctor_command,
    handle_job_command,
    handle_jobs_command,
    handle_mcp_command,
    handle_permissions_command,
    handle_plugins_command,
    handle_profile_command,
    handle_skills_command,
    handle_status_command,
)
from archon.config import ensure_dirs, load_config
from archon.control.session_controller import (
    extract_explicit_job_status_ref,
    is_ai_news_request,
    is_explicit_job_list_request,
)
from archon.history import new_session_id, save_exchange
from archon.news.runner import get_or_build_news_digest
from archon.news.state import load_cached_digest, load_news_state, news_state_path
from archon.security.redaction import sanitize_terminal_notice_text
from archon.ux.operator_messages import (
    build_approval_result_message,
    build_approvals_overview_message,
    build_blocked_action_message,
    build_operator_help_text,
)
from archon.safety import Level
from archon.ux.events import ActivityEvent, UXEvent
from archon.ux.telegram_renderer import OutputBatchCollector, TelegramRenderer

if TYPE_CHECKING:
    from archon.agent import Agent


MAX_TELEGRAM_MESSAGE_LEN = DEFAULT_TELEGRAM_MESSAGE_LIMIT
_TELEGRAM_BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Connect and show basics"),
    ("help", "Show command guide"),
    ("status", "Inspect session state"),
    ("new", "Fresh chat context"),
    ("compact", "Reduce context pressure"),
    ("context", "Inspect context state"),
    ("cost", "Show token usage"),
    ("jobs", "List background jobs"),
    ("approvals", "Inspect approval state"),
    ("skills", "List available skills"),
    ("mcp", "Inspect MCP servers"),
    ("reset", "Reset chat session"),
)


class _ActivitySinkTextProxy:
    """Adapter to route free-form text notices through the ActivityEvent sink."""

    def __init__(self, sink: Callable[[ActivityEvent], None]):
        self._sink = sink

    def emit_text(self, text: str) -> None:
        try:
            self._sink(ActivityEvent(source="telegram", message=text))
        except Exception:
            return


def headless_confirmer(command: str, level: Level) -> bool:
    """Confirmer for non-interactive transports: allow safe, block everything else."""
    if level == Level.SAFE:
        return True
    if level == Level.FORBIDDEN:
        print(f"[telegram] FORBIDDEN: {command}", file=sys.stderr)
        return False
    print(f"[telegram] BLOCKED (needs interactive confirm): {command}", file=sys.stderr)
    return False


class TelegramAdapter:
    """Long-polling Telegram adapter with one Agent session per chat."""

    def __init__(
        self,
        token: str,
        allowed_user_ids: list[int] | set[int],
        agent_factory: Callable[[], "Agent"],
        poll_timeout_sec: int = 30,
    ):
        self.token = token.strip()
        self.allowed_user_ids = {int(x) for x in allowed_user_ids}
        self.agent_factory = agent_factory
        self.poll_timeout_sec = max(1, int(poll_timeout_sec))

        if not self.token:
            raise ValueError("Telegram bot token is required")
        if not self.allowed_user_ids:
            raise ValueError("Telegram allowed_user_ids must not be empty")

        self._bot = TelegramBotClient(self.token)
        self._offset: int | None = None
        self._agents: dict[int, Agent] = {}
        self._history_session_ids: dict[int, str] = {}
        self._session_to_chat: dict[str, int] = {}
        self._batch_collectors: dict[int, OutputBatchCollector] = {}
        self._approval_always_on_chats: set[int] = set()
        self._approval_elevated_until: dict[int, float] = {}
        self._approve_next_tokens: dict[int, int] = {}
        self._pending_approvals: dict[int, dict] = {}
        self._active_replay_approval_ids: dict[int, str] = {}
        self._current_request_ctx: dict[int, dict] = {}
        self._activity_sink: Callable[[ActivityEvent], None] | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_synced = False
        self._commands_synced = False
        self._last_error_signature: tuple[str, str] | None = None
        self._last_error_at: float = 0.0
        self._telegram_renderer = TelegramRenderer()
        self._polling_disabled_due_to_conflict = False

    def set_activity_sink(self, sink: Callable[[ActivityEvent], None] | None) -> None:
        """Register an optional cross-surface activity sink."""
        self._activity_sink = sink

    def start(self) -> None:
        """Start polling in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="archon-telegram",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop background polling thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_forever(self) -> None:
        """Run polling loop in foreground until interrupted."""
        self._stop_event.clear()
        self._run_loop()

    def _run_loop(self) -> None:
        """Main polling loop."""
        self._sync_bot_commands()
        self._sync_startup_offset()
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates()
                for update in updates:
                    if self._stop_event.is_set():
                        break
                    self._process_update(update)
                    self._offset = int(update["update_id"]) + 1
            except KeyboardInterrupt:
                raise
            except Exception as e:
                if _is_conflicting_get_updates_error(e):
                    self._polling_disabled_due_to_conflict = True
                    print(
                        f"[telegram] Polling disabled ({type(e).__name__}: {e})",
                        file=sys.stderr,
                    )
                    break
                self._log_poll_error(e, source="poll")
                if self._stop_event.wait(2.0):
                    break

    def _sync_startup_offset(self) -> None:
        """Drop queued Telegram updates on startup to avoid replaying stale commands.

        Telegram getUpdates keeps undelivered updates while the bot is offline. Since
        Archon's polling offset is currently in-memory only, restarting Archon would
        otherwise replay old commands (including dangerous ones) when polling resumes.
        """
        if self._startup_synced:
            return
        if self._offset is not None:
            self._startup_synced = True
            return
        try:
            data = self._api_call(
                "getUpdates",
                {"timeout": 0, "allowed_updates": ["message", "callback_query"]},
                timeout=5,
            )
            result = data.get("result")
            if not isinstance(result, list) or not result:
                return
            latest_update_id = max(int(update["update_id"]) for update in result if "update_id" in update)
            self._offset = latest_update_id + 1
            self._startup_synced = True
            print(
                f"[telegram] Skipped {len(result)} pending update(s) on startup",
                file=sys.stderr,
            )
        except Exception as e:
            self._log_poll_error(e, source="startup_sync")

    def _sync_bot_commands(self) -> None:
        """Replace the remote Telegram command menu with Archon's current surface."""
        if self._commands_synced:
            return
        try:
            self._api_call(
                "setMyCommands",
                {
                    "commands": [
                        {"command": command, "description": description}
                        for command, description in _TELEGRAM_BOT_COMMANDS
                    ]
                },
                timeout=10,
            )
            self._commands_synced = True
        except Exception as e:
            self._log_poll_error(e, source="command_sync")

    def _log_poll_error(self, error: Exception, *, source: str) -> None:
        error_type = type(error).__name__
        message = str(error)
        signature = (error_type, message)
        now = time.time()
        if self._last_error_signature == signature and (now - self._last_error_at) < 5.0:
            return
        self._last_error_signature = signature
        self._last_error_at = now
        if source == "startup_sync":
            print(f"[telegram] Startup sync skipped ({error_type}: {message})", file=sys.stderr)
            return
        if source == "command_sync":
            print(f"[telegram] Command sync skipped ({error_type}: {message})", file=sys.stderr)
            return
        print(f"[telegram] Poll error: {error_type}: {message}", file=sys.stderr)

    def _get_updates(self) -> list[dict]:
        payload: dict[str, object] = {
            "timeout": self.poll_timeout_sec,
            "allowed_updates": ["message", "callback_query"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset
        attempts = 0
        while True:
            try:
                data = self._api_call("getUpdates", payload, timeout=self.poll_timeout_sec + 10)
                break
            except Exception as e:
                if attempts >= 1 or not _is_transient_get_updates_error(e):
                    raise
                attempts += 1
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Telegram getUpdates returned invalid result payload")
        return result

    def _process_update(self, update: dict) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return

        if user_id not in self.allowed_user_ids:
            print(f"[telegram] Ignoring message from unauthorized user_id={user_id}", file=sys.stderr)
            return

        emit_receive_notice = not bool(message.get("_archon_internal_replay"))

        voice = message.get("voice")
        if isinstance(voice, dict):
            if emit_receive_notice:
                self._emit_activity(f"voice received from {chat_id}")
            self._handle_voice_or_audio_message(chat_id, user_id, voice, kind="voice")
            return

        audio = message.get("audio")
        if isinstance(audio, dict):
            if emit_receive_notice:
                self._emit_activity(f"audio received from {chat_id}")
            self._handle_voice_or_audio_message(chat_id, user_id, audio, kind="audio")
            return

        text = message.get("text")
        if not isinstance(text, str):
            return
        if emit_receive_notice:
            self._emit_activity(f"received from {chat_id}: {self._preview_text(text)}")

        body = text.strip()
        if not body:
            return
        cmd = body.split(maxsplit=1)[0].lower()

        if cmd == "/start":
            self._send_text_and_record(
                chat_id,
                body,
                build_operator_help_text(
                    intro="Archon is connected.",
                    core="/status, /approvals, /jobs, /skills, /mcp, /reset",
                    context="/new, /clear, /compact, /context, /cost",
                    footer="Use /help for the compact command guide.",
                ),
            )
            return

        if cmd == "/help":
            self._send_text_and_record(
                chat_id,
                body,
                build_operator_help_text(
                    core="/status, /approvals, /jobs, /skills, /mcp, /reset",
                    context="/new, /clear, /compact, /context, /cost",
                    advanced="/doctor, /permissions, /plugins, /profile, /jobs show <job-id>, "
                    "/approve, /deny, /approve_next, /news, /news_status",
                    footer="Dangerous commands can be approved with inline buttons or /approve.",
                ),
            )
            return

        if cmd == "/reset":
            agent = self._agents.pop(chat_id, None)
            if agent is not None:
                session_id = str(getattr(agent, "session_id", "") or "").strip()
                if session_id:
                    self._session_to_chat.pop(session_id, None)
                agent.reset()
            history_session_id = self._history_session_ids.pop(chat_id, None)
            if history_session_id:
                self._session_to_chat.pop(str(history_session_id), None)
            collector = self._batch_collectors.pop(chat_id, None)
            if collector is not None:
                collector.cancel()
            self._pending_approvals.pop(chat_id, None)
            self._send_text_and_record(chat_id, body, "History cleared for this Telegram chat.")
            return

        if cmd == "/news_status":
            self._send_text_and_record(chat_id, body, self._build_news_status_text())
            return

        if cmd == "/news":
            self._send_typing(chat_id)
            self._send_text_and_record(chat_id, body, self._build_news_reply(body))
            return

        if self._is_ai_news_request(body):
            self._send_typing(chat_id)
            self._send_text_and_record(chat_id, body, self._build_news_reply(body))
            return

        raw = (body or "").strip()
        job_agent = self._get_local_shell_agent(chat_id, body)
        lowered = raw.lower()
        if (
            lowered.startswith("/job cancel")
            or lowered.startswith("/jobs purge")
        ) and (
            job_agent is None or self._is_degraded_local_agent(job_agent)
        ):
            self._send_text_and_record(
                chat_id,
                body,
                "Local command unavailable: live chat agent failed to start.",
            )
            return

        handled, msg = handle_jobs_command(job_agent, body)
        if handled:
            self._send_text_and_record(
                chat_id,
                body,
                self._format_degraded_local_response(job_agent, body, msg),
            )
            return

        native_job_ref = extract_explicit_job_status_ref(body)
        if native_job_ref:
            handled, msg = handle_jobs_command(job_agent, f"/jobs show {native_job_ref}")
            if handled:
                self._send_text_and_record(
                    chat_id,
                    body,
                    self._format_degraded_local_response(job_agent, body, msg),
                )
                return

        if is_explicit_job_list_request(body):
            handled, msg = handle_jobs_command(job_agent, "/jobs active")
            if handled:
                self._send_text_and_record(
                    chat_id,
                    body,
                    self._format_degraded_local_response(job_agent, body, msg),
                )
                return

        handled, msg = handle_job_command(job_agent, body)
        if handled:
            self._send_text_and_record(
                chat_id,
                body,
                self._format_degraded_local_response(job_agent, body, msg),
            )
            return

        if cmd == "/approve_next":
            self._approve_next_tokens[chat_id] = self._approve_next_tokens.get(chat_id, 0) + 1
            self._send_text_and_record(
                chat_id,
                body,
                build_approval_result_message(
                    result="allow_once_armed",
                    dangerous_mode=self._dangerous_mode_enabled(chat_id),
                    pending_request=self._pending_request_preview(chat_id),
                    allow_once_remaining=self._approve_next_tokens.get(chat_id, 0),
                    next_step="one_future_dangerous_action_allowed",
                ),
            )
            return

        if cmd == "/approvals":
            self._send_text_and_record(chat_id, body, self._handle_approvals_command(body, chat_id))
            return

        if cmd == "/approve":
            self._send_text_and_record(chat_id, body, self._approve_pending_request(chat_id))
            return

        if cmd == "/deny":
            self._send_text_and_record(chat_id, body, self._deny_pending_request(chat_id))
            return

        if self._handle_local_shell_command(chat_id, body):
            return

        self._handle_chat_body(chat_id, user_id, body, history_user_text=body)

    def _handle_local_shell_command(self, chat_id: int, body: str) -> bool:
        raw = (body or "").strip()
        if not raw.startswith("/"):
            return False
        cmd = raw.split(maxsplit=1)[0].lower()
        if cmd not in {
            "/status",
            "/new",
            "/clear",
            "/cost",
            "/compact",
            "/context",
            "/doctor",
            "/permissions",
            "/skills",
            "/plugins",
            "/mcp",
            "/profile",
        }:
            return False

        agent = self._get_local_shell_agent(chat_id, body)
        if agent is None:
            self._send_text_and_record(
                chat_id,
                body,
                "Local command unavailable: live chat agent failed to start.",
            )
            return True
        for handler in (
            handle_status_command,
            handle_clear_command,
            handle_cost_command,
            handle_compact_command,
            handle_context_command,
            handle_doctor_command,
            handle_permissions_command,
            handle_skills_command,
            handle_plugins_command,
            handle_mcp_command,
            handle_profile_command,
        ):
            handled, msg = handler(agent, body)
            if handled:
                response = self._format_degraded_local_response(agent, body, msg)
                self._send_text_and_record(chat_id, body, response)
                return True
        return False

    def _get_local_shell_agent(self, chat_id: int, body: str):
        try:
            return self._get_or_create_chat_agent(chat_id)
        except Exception as e:
            fallback_agent = self._build_local_shell_fallback_agent(body)
            if fallback_agent is not None:
                setattr(fallback_agent, "_local_shell_fallback_error", f"{type(e).__name__}: {e}")
            return fallback_agent

    def _build_local_shell_fallback_agent(self, body: str):
        raw = (body or "").strip()
        parts = raw.split()
        if not parts:
            return None
        cmd = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""

        # Only use fallback for read-only inspection commands.
        if cmd == "/jobs" and sub == "purge":
            return None
        if cmd == "/job" and sub == "cancel":
            return None
        if cmd == "/skills" and sub not in {"", "list", "show", "status"}:
            return None
        if cmd == "/profile" and sub not in {"", "show", "status", "list"}:
            return None
        if cmd in {"/status", "/new", "/clear", "/cost", "/context", "/doctor", "/permissions", "/plugins", "/mcp"} or (
            cmd == "/skills" and sub in {"", "list", "show", "status"}
        ) or (cmd == "/profile" and sub in {"", "show", "status", "list"}) or cmd == "/jobs" or cmd == "/job":
            cfg = load_config()
            llm_cfg = getattr(cfg, "llm", None)
            default_profile = str(getattr(getattr(cfg, "orchestrator", None), "default_profile", "") or "").strip()
            if not default_profile:
                default_profile = "default"
            return SimpleNamespace(
                config=cfg,
                llm=SimpleNamespace(
                    provider=str(getattr(llm_cfg, "provider", "") or ""),
                    model=str(getattr(llm_cfg, "model", "") or ""),
                    api_key=str(getattr(llm_cfg, "api_key", "") or ""),
                ),
                policy_profile=default_profile,
                total_input_tokens=0,
                total_output_tokens=0,
                history=[],
            )
        return None

    def _is_degraded_local_agent(self, agent) -> bool:
        return bool(str(getattr(agent, "_local_shell_fallback_error", "") or "").strip())

    def _describe_degraded_local_source(self, body: str) -> str:
        raw = (body or "").strip()
        cmd = raw.split(maxsplit=1)[0].lower() if raw else ""
        if cmd in {"/job", "/jobs"}:
            return "using local job store"
        return "using local fallback snapshot"

    def _format_degraded_local_response(self, agent, body: str, message: str) -> str:
        fallback_error = str(getattr(agent, "_local_shell_fallback_error", "") or "").strip()
        if not fallback_error:
            return message
        return (
            f"Degraded mode: live chat agent unavailable ({fallback_error}); "
            f"{self._describe_degraded_local_source(body)}.\n"
            f"{message}"
        )

    def _get_or_create_chat_agent(self, chat_id: int) -> "Agent":
        agent = self._agents.get(chat_id)
        history_session_id = self._history_session_id(chat_id)
        if agent is None:
            agent = self.agent_factory()
            agent.log_label = f"telegram chat={chat_id}"
            agent.session_id = history_session_id
            self._session_to_chat[history_session_id] = chat_id
            if callable(self._activity_sink):
                agent.terminal_activity_feed = _ActivitySinkTextProxy(self._activity_sink)
            self._wire_chat_confirmer(agent, chat_id)
            self._wire_chat_route_progress(agent, chat_id)
            self._agents[chat_id] = agent
        else:
            agent.log_label = f"telegram chat={chat_id}"
            agent.session_id = history_session_id
            self._session_to_chat[history_session_id] = chat_id
            if callable(self._activity_sink):
                agent.terminal_activity_feed = _ActivitySinkTextProxy(self._activity_sink)
        return agent

    def _handle_voice_or_audio_message(
        self,
        chat_id: int,
        user_id: int,
        payload: dict,
        *,
        kind: str,
    ) -> None:
        file_id = payload.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            self._send_text(chat_id, f"{kind.capitalize()} message error: missing file_id.")
            return

        mime_type = payload.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type.strip():
            mime_type = "audio/ogg" if kind == "voice" else "audio/mpeg"

        try:
            self._send_typing(chat_id)
            file_info = self._bot.get_file(file_id, timeout=10)
            file_path = file_info.get("file_path")
            if not isinstance(file_path, str) or not file_path.strip():
                raise RuntimeError("Telegram getFile returned no file_path")
            audio_bytes = self._bot.download_file(file_path, timeout=20)
            transcript = transcribe_audio_bytes(audio_bytes, mime_type)
        except Exception as e:
            self._send_text(chat_id, f"Voice transcription error: {type(e).__name__}: {e}")
            return

        transcript = transcript.strip()
        if not transcript:
            self._send_text(chat_id, "Voice transcription error: empty transcript.")
            return

        if self._is_ai_news_request(transcript):
            self._send_typing(chat_id)
            news_reply = self._build_news_reply(f"[{kind}] {transcript}")
            self._send_text_and_record(chat_id, f"[{kind}] {transcript}", news_reply)
            self._send_voice_reply_audio(chat_id, news_reply)
            return

        reply_text = self._handle_chat_body(
            chat_id,
            user_id,
            transcript,
            history_user_text=f"[{kind}] {transcript}",
        )
        if isinstance(reply_text, str):
            self._send_voice_reply_audio(chat_id, reply_text)

    def _is_ai_news_request(self, body: str) -> bool:
        return is_ai_news_request(body)

    def _handle_chat_body(
        self,
        chat_id: int,
        user_id: int,
        body: str,
        *,
        history_user_text: str,
    ) -> None:
        agent = None
        try:
            agent = self._get_or_create_chat_agent(chat_id)

            # Wire typing indicator: fire on every LLM call and tool call
            agent.on_thinking = lambda: self._send_typing(chat_id)
            agent.on_tool_call = lambda name, args: self._send_typing(chat_id)

            self._send_typing(chat_id)
            auto_skill_changed, auto_skill_msg = _maybe_auto_activate_skill(agent, body)
            if auto_skill_changed and auto_skill_msg:
                self._send_text(chat_id, auto_skill_msg)
                skill_name = auto_skill_msg.split(":", 1)[-1].strip()
                self._emit_activity(f"skill auto-activated for {chat_id}: {skill_name}")
            self._current_request_ctx[chat_id] = {
                "user_id": user_id,
                "chat_id": chat_id,
                "user_text": body,
                "blocked_approval_id": None,
                "route_progress_turn_id": None,
            }
            response = agent.run(body)
        except Exception as e:
            response = f"Error: {type(e).__name__}: {e}"
            turn_id = getattr(agent, "last_turn_id", "") if agent is not None else ""
            turn_info = f" turn={turn_id}" if turn_id else ""
            print(f"[telegram] Agent error for chat {chat_id}{turn_info}: {response}", file=sys.stderr)
        finally:
            ctx = self._current_request_ctx.pop(chat_id, None)

        if ctx and ctx.get("blocked_approval_id") and looks_like_safety_gate_rejection(response):
            # Approval prompt already sent from the confirmer; suppress the extra generic rejection.
            return None

        final_text = response or "(empty response)"
        self._send_text_and_record(chat_id, history_user_text, final_text)
        return final_text

    def _send_voice_reply_audio(self, chat_id: int, reply_text: str) -> None:
        text = str(reply_text or "").strip()
        if not text or looks_like_safety_gate_rejection(text) or text.startswith("Error: "):
            return
        try:
            wav_bytes, mime_type = synthesize_speech_wav(text)
            try:
                ogg_bytes, ogg_mime = convert_wav_to_ogg_opus(wav_bytes)
                self._bot.send_voice_bytes(
                    chat_id,
                    filename="archon-reply.ogg",
                    data=ogg_bytes,
                    mime_type=ogg_mime,
                    caption=None,
                    timeout=25,
                )
                self._emit_activity(f"voice reply sent to {chat_id}")
            except Exception as convert_or_upload_error:
                print(
                    f"[telegram] Voice-note path fallback for chat {chat_id}: "
                    f"{type(convert_or_upload_error).__name__}: {convert_or_upload_error}",
                    file=sys.stderr,
                )
                self._bot.send_document_bytes(
                    chat_id,
                    filename="archon-reply.wav",
                    data=wav_bytes,
                    mime_type=mime_type,
                    caption="Archon voice reply",
                    timeout=25,
                )
                self._emit_activity(
                    f"voice reply sent to {chat_id} (wav fallback: "
                    f"{type(convert_or_upload_error).__name__})"
                )
        except Exception as e:
            print(f"[telegram] Voice reply TTS error for chat {chat_id}: {type(e).__name__}: {e}", file=sys.stderr)
            self._emit_activity(f"voice reply failed for {chat_id}: {type(e).__name__}: {e}")

    def _handle_callback_query(self, callback: dict) -> None:
        callback_id = callback.get("id")
        data = callback.get("data")
        sender = callback.get("from") or {}
        user_id = sender.get("id")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(callback_id, str):
            return
        if not isinstance(user_id, int) or user_id not in self.allowed_user_ids:
            answer_callback_query_safe(self._bot, callback_id, text="Unauthorized", show_alert=True)
            return
        if not isinstance(chat_id, int):
            answer_callback_query_safe(self._bot, callback_id, text="Missing chat", show_alert=True)
            return
        parsed = parse_approval_callback_data(data)
        if parsed is None:
            answer_callback_query_safe(self._bot, callback_id, text="Unknown action")
            return
        approval_id, action = parsed
        pending = self._pending_approvals.get(chat_id)
        if not pending or pending.get("approval_id") != approval_id:
            answer_callback_query_safe(self._bot, callback_id, text="Approval request expired")
            return
        if self._pending_is_expired(pending):
            pending["status"] = "expired"
            self._pending_approvals.pop(chat_id, None)
            answer_callback_query_safe(self._bot, callback_id, text="Approval expired")
            return
        if pending.get("status") != "pending":
            answer_callback_query_safe(self._bot, callback_id, text="Already handled")
            return

        if action == APPROVAL_ACTION_APPROVE:
            answer_callback_query_safe(self._bot, callback_id, text="Approved. Replaying request…")
            self._update_approval_prompt(chat_id, pending, "Approved. Replaying request…")
            self._replay_pending_request(chat_id, pending, elevated_ttl_sec=None)
            return

        if action == APPROVAL_ACTION_ALLOW15:
            answer_callback_query_safe(self._bot, callback_id, text="Unlocked for 15m. Replaying…")
            self._update_approval_prompt(chat_id, pending, "Unlocked for 15m. Replaying request…")
            self._replay_pending_request(chat_id, pending, elevated_ttl_sec=ELEVATED_APPROVAL_TTL_SEC)
            return

        if action == APPROVAL_ACTION_DENY:
            pending["status"] = "denied"
            self._pending_approvals.pop(chat_id, None)
            answer_callback_query_safe(self._bot, callback_id, text="Denied")
            self._update_approval_prompt(chat_id, pending, "Denied.")
            return

        answer_callback_query_safe(self._bot, callback_id, text="Unknown approval action")

    def _wire_chat_confirmer(self, agent: "Agent", chat_id: int) -> None:
        """Patch agent tools confirmer so Telegram chat can approve dangerous actions."""
        tools = getattr(agent, "tools", None)
        if tools is None or not hasattr(tools, "confirmer"):
            return
        tools.confirmer = lambda command, level, _chat_id=chat_id: self._confirm_for_chat(
            _chat_id, command, level
        )

    def _wire_chat_route_progress(self, agent: "Agent", chat_id: int) -> None:
        """Register one route-progress hook per Telegram chat agent."""
        if getattr(agent, "_telegram_route_progress_wired", False):
            return
        hooks = getattr(agent, "hooks", None)
        if hooks is None or not hasattr(hooks, "register"):
            return
        hooks.register(
            "orchestrator.route",
            lambda event, _chat_id=chat_id: self._handle_route_progress_event(_chat_id, event.payload or {}),
        )
        setattr(agent, "_telegram_route_progress_wired", True)

    def _handle_route_progress_event(self, chat_id: int, payload: dict) -> None:
        lane = str(payload.get("lane", "")).strip().lower()
        if lane != "job":
            return
        ctx = self._current_request_ctx.get(chat_id)
        if not isinstance(ctx, dict):
            return
        turn_id = str(payload.get("turn_id", "")).strip()
        if turn_id and ctx.get("route_progress_turn_id") == turn_id:
            return
        ctx["route_progress_turn_id"] = turn_id
        self._send_text(chat_id, self._format_route_progress_text(payload))
        lane = str(payload.get("lane", "")).strip().lower() or "operator"
        reason = str(payload.get("reason", "")).strip().replace("_", " ")
        detail = f"route progress for {chat_id}: {lane}"
        if reason:
            detail += f" | {reason}"
        self._emit_activity(detail)

    def _format_route_progress_text(self, payload: dict) -> str:
        lane = str(payload.get("lane", "")).strip().lower() or "operator"
        reason = str(payload.get("reason", "")).strip().replace("_", " ")
        text = f"Working... route: {lane}"
        if reason:
            text += f" | {reason}"
        return text

    def _confirm_for_chat(self, chat_id: int, command: str, level: Level) -> bool:
        """Per-chat Telegram confirmer with one-shot and sticky approval modes."""
        if level == Level.SAFE:
            return True
        if level == Level.FORBIDDEN:
            print(f"[telegram] FORBIDDEN: {command}", file=sys.stderr)
            self._emit_activity(f"approval forbidden for {chat_id}: {truncate_approval_command(command)}")
            self._send_text(chat_id, "FORBIDDEN action blocked by safety policy.")
            return False

        if self._is_chat_elevated(chat_id):
            print(f"[telegram] APPROVED (mode=elevated): {command}", file=sys.stderr)
            self._emit_activity(f"approval allowed for {chat_id}: {truncate_approval_command(command)}")
            return True

        active_replay_id = self._active_replay_approval_ids.get(chat_id)
        if active_replay_id:
            print(f"[telegram] APPROVED (mode=request-replay): {command}", file=sys.stderr)
            self._emit_activity(f"approval allowed for {chat_id}: {truncate_approval_command(command)}")
            return True

        if chat_id in self._approval_always_on_chats:
            print(f"[telegram] APPROVED (mode=always): {command}", file=sys.stderr)
            self._emit_activity(f"approval allowed for {chat_id}: {truncate_approval_command(command)}")
            return True

        tokens = self._approve_next_tokens.get(chat_id, 0)
        if tokens > 0:
            remaining = tokens - 1
            if remaining:
                self._approve_next_tokens[chat_id] = remaining
            else:
                self._approve_next_tokens.pop(chat_id, None)
            print(
                f"[telegram] APPROVED (mode=next, remaining={remaining}): {command}",
                file=sys.stderr,
            )
            self._emit_activity(f"approval allowed for {chat_id}: {truncate_approval_command(command)}")
            return True

        print(f"[telegram] BLOCKED (needs Telegram approval): {command}", file=sys.stderr)
        self._emit_activity(f"approval blocked for {chat_id}: {truncate_approval_command(command)}")
        self._queue_pending_approval(chat_id, command)
        return False

    def _handle_approvals_command(self, body: str, chat_id: int) -> str:
        parts = [p.strip().lower() for p in body.split() if p.strip()]
        subcmd = parts[1] if len(parts) > 1 else ""
        pending_request = self._pending_request_preview(chat_id)
        is_elevated = self._is_chat_elevated(chat_id)
        elevated_until = self._approval_elevated_until.get(chat_id, 0.0)
        elevated_remaining = max(0, int(elevated_until - time.time())) if is_elevated else 0
        dangerous_mode = chat_id in self._approval_always_on_chats or is_elevated
        allow_once_remaining = self._approve_next_tokens.get(chat_id, 0)

        if subcmd in {"on", "enable"}:
            self._approval_always_on_chats.add(chat_id)
            return build_approvals_overview_message(
                result="dangerous_mode_enabled",
                dangerous_mode=True,
                pending_request=pending_request,
                allow_once_remaining=allow_once_remaining,
                elevated_ttl_sec=elevated_remaining,
            )
        if subcmd in {"off", "disable"}:
            self._approval_always_on_chats.discard(chat_id)
            self._approval_elevated_until.pop(chat_id, None)
            return build_approvals_overview_message(
                result="dangerous_mode_disabled",
                dangerous_mode=False,
                pending_request=pending_request,
                allow_once_remaining=allow_once_remaining,
            )
        if subcmd in {"once", "next"}:
            self._approve_next_tokens[chat_id] = self._approve_next_tokens.get(chat_id, 0) + 1
            return build_approval_result_message(
                result="allow_once_armed",
                dangerous_mode=self._dangerous_mode_enabled(chat_id),
                pending_request=pending_request,
                allow_once_remaining=self._approve_next_tokens.get(chat_id, 0),
                next_step="one_future_dangerous_action_allowed",
            )
        if subcmd in {"unlock"}:
            self._approval_elevated_until[chat_id] = time.time() + ELEVATED_APPROVAL_TTL_SEC
            return build_approvals_overview_message(
                result="dangerous_mode_enabled",
                dangerous_mode=True,
                pending_request=pending_request,
                allow_once_remaining=allow_once_remaining,
                elevated_ttl_sec=ELEVATED_APPROVAL_TTL_SEC,
            )
        if subcmd in {"lock"}:
            self._approval_elevated_until.pop(chat_id, None)
            self._approval_always_on_chats.discard(chat_id)
            return build_approvals_overview_message(
                result="dangerous_mode_disabled",
                dangerous_mode=False,
                pending_request=pending_request,
                allow_once_remaining=allow_once_remaining,
            )

        return build_approvals_overview_message(
            dangerous_mode=dangerous_mode,
            pending_request=pending_request,
            allow_once_remaining=allow_once_remaining,
            elevated_ttl_sec=elevated_remaining,
        )

    def _is_chat_elevated(self, chat_id: int) -> bool:
        until = self._approval_elevated_until.get(chat_id)
        if until is None:
            return False
        if until <= time.time():
            self._approval_elevated_until.pop(chat_id, None)
            return False
        return True

    def _dangerous_mode_enabled(self, chat_id: int) -> bool:
        return chat_id in self._approval_always_on_chats or self._is_chat_elevated(chat_id)

    def _pending_is_expired(self, pending: dict) -> bool:
        expires_at = pending.get("expires_at")
        return isinstance(expires_at, (int, float)) and expires_at <= time.time()

    def _pending_approval_for_status(self, chat_id: int) -> dict | None:
        pending = self._pending_approvals.get(chat_id)
        if not pending:
            return None
        if self._pending_is_expired(pending):
            pending["status"] = "expired"
            self._pending_approvals.pop(chat_id, None)
            return None
        return pending

    def _pending_request_preview(self, chat_id: int) -> str:
        pending = self._pending_approval_for_status(chat_id) or {}
        return str(pending.get("blocked_command_preview") or "")

    def _queue_pending_approval(self, chat_id: int, command: str) -> None:
        ctx = self._current_request_ctx.get(chat_id) or {}
        user_text = str(ctx.get("user_text") or "")
        if not user_text:
            self._send_text(
                chat_id,
                build_blocked_action_message(
                    truncate_approval_command(command),
                    replay_effect="replays_pending_request_when_original_message_is_available",
                    extra_lines=("state=original_request_missing",),
                ),
            )
            return
        existing = self._pending_approvals.get(chat_id)
        if existing and existing.get("status") == "pending" and not self._pending_is_expired(existing):
            # Keep only the latest blocked request so /approve replays current intent.
            existing["created_at"] = time.time()
            existing["expires_at"] = time.time() + PENDING_APPROVAL_TTL_SEC
            existing["user_text"] = user_text
            existing["user_id"] = ctx.get("user_id")
            existing["blocked_command_preview"] = truncate_approval_command(command)
            self._refresh_pending_approval_prompt(chat_id, existing)
            if ctx is not None:
                ctx["blocked_approval_id"] = existing.get("approval_id")
            return

        approval_id = secrets.token_hex(4)
        pending = {
            "approval_id": approval_id,
            "status": "pending",
            "created_at": time.time(),
            "expires_at": time.time() + PENDING_APPROVAL_TTL_SEC,
            "user_text": user_text,
            "user_id": ctx.get("user_id"),
            "blocked_command_preview": truncate_approval_command(command),
            "approval_message_id": None,
        }
        self._pending_approvals[chat_id] = pending
        if ctx is not None:
            ctx["blocked_approval_id"] = approval_id
        self._send_pending_approval_prompt(chat_id, pending)

    def _refresh_pending_approval_prompt(self, chat_id: int, pending: dict) -> None:
        message_id = pending.get("approval_message_id")
        if not isinstance(message_id, int):
            self._send_pending_approval_prompt(chat_id, pending)
            return
        text = build_pending_approval_text(str(pending.get("blocked_command_preview") or ""))
        try:
            self._bot.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=build_approval_reply_markup(str(pending.get("approval_id") or "")),
            )
        except Exception:
            self._send_pending_approval_prompt(chat_id, pending)

    def _send_pending_approval_prompt(self, chat_id: int, pending: dict) -> None:
        text = build_pending_approval_text(str(pending.get("blocked_command_preview") or ""))
        try:
            result = self._bot.send_message(
                chat_id,
                text,
                timeout=15,
                disable_web_page_preview=True,
                reply_markup=build_approval_reply_markup(str(pending.get("approval_id") or "")),
            )
            message_id = result.get("message_id")
            if isinstance(message_id, int):
                pending["approval_message_id"] = message_id
        except Exception:
            self._send_text(
                chat_id,
                build_blocked_action_message(
                    str(pending.get("blocked_command_preview") or ""),
                    heading="Dangerous action blocked.",
                ),
            )

    def _update_approval_prompt(self, chat_id: int, pending: dict, status_text: str) -> None:
        message_id = pending.get("approval_message_id")
        if not isinstance(message_id, int):
            return
        text = build_approval_status_text(
            str(pending.get("blocked_command_preview") or ""),
            status_text,
        )
        try:
            self._bot.edit_message_text(chat_id, message_id, text, reply_markup={"inline_keyboard": []})
        except Exception:
            pass

    def _approve_pending_request(self, chat_id: int) -> str:
        pending = self._pending_approvals.get(chat_id)
        if not pending:
            return build_approval_result_message(
                result="no_pending_request",
                pending_request="none",
            )
        if self._pending_is_expired(pending):
            pending["status"] = "expired"
            self._pending_approvals.pop(chat_id, None)
            return build_approval_result_message(
                result="expired",
                pending_request="none",
            )
        if pending.get("status") != "pending":
            return build_approval_result_message(
                result=f"already_{pending.get('status')}",
                pending_request=str(pending.get("blocked_command_preview") or ""),
            )
        self._update_approval_prompt(chat_id, pending, "Approved. Replaying request…")
        return self._replay_pending_request(chat_id, pending, elevated_ttl_sec=None)

    def _deny_pending_request(self, chat_id: int) -> str:
        pending = self._pending_approvals.get(chat_id)
        if not pending:
            return build_approval_result_message(
                result="no_pending_request",
                pending_request="none",
            )
        preview = str(pending.get("blocked_command_preview") or "")
        pending["status"] = "denied"
        self._pending_approvals.pop(chat_id, None)
        self._update_approval_prompt(chat_id, pending, "Denied.")
        return build_approval_result_message(
            result="denied",
            denied_request=preview,
            dangerous_mode=self._dangerous_mode_enabled(chat_id),
            pending_request="none",
            allow_once_remaining=self._approve_next_tokens.get(chat_id, 0),
        )

    def _replay_pending_request(self, chat_id: int, pending: dict, elevated_ttl_sec: int | None) -> str:
        user_text = str(pending.get("user_text") or "").strip()
        preview = str(pending.get("blocked_command_preview") or "")
        if not user_text:
            pending["status"] = "error"
            self._pending_approvals.pop(chat_id, None)
            return build_approval_result_message(
                result="approved_replay_unavailable",
                replayed_request=preview,
                dangerous_mode=self._dangerous_mode_enabled(chat_id),
                pending_request="none",
                allow_once_remaining=self._approve_next_tokens.get(chat_id, 0),
            )
        if elevated_ttl_sec:
            self._approval_elevated_until[chat_id] = time.time() + int(elevated_ttl_sec)
        approval_id = str(pending.get("approval_id") or "")
        pending["status"] = "approved"
        self._active_replay_approval_ids[chat_id] = approval_id
        replay_user_id = pending.get("user_id")
        if not isinstance(replay_user_id, int):
            replay_user_id = chat_id
        try:
            self._handle_message(
                {
                    "text": user_text,
                    "chat": {"id": chat_id},
                    "from": {"id": replay_user_id},
                    "_archon_internal_replay": True,
                }
            )
        finally:
            self._active_replay_approval_ids.pop(chat_id, None)
            self._pending_approvals.pop(chat_id, None)
        return build_approval_result_message(
            result="approved_replaying",
            replayed_request=preview,
            dangerous_mode=self._dangerous_mode_enabled(chat_id),
            pending_request="none",
            allow_once_remaining=self._approve_next_tokens.get(chat_id, 0),
            next_step="original_request_replayed_now",
        )

    def _send_typing(self, chat_id: int) -> None:
        """Send typing indicator (best effort)."""
        try:
            self._bot.send_typing(chat_id, timeout=5)
        except Exception:
            pass

    def _build_news_reply(self, body: str) -> str:
        tokens = [part.strip().lower() for part in body.split()]
        force_refresh = any(tok in {"refresh", "force", "--force"} for tok in tokens[1:])

        try:
            ensure_dirs()
            config = load_config()
            result = get_or_build_news_digest(config, force_refresh=force_refresh)
        except Exception as e:
            return f"News error: {type(e).__name__}: {e}"

        if result.digest is None:
            return f"news status: {result.status}\nreason: {result.reason or '(none)'}"

        prefix = ""
        if result.reason == "cache_hit":
            prefix = "[news] Using cached digest for today.\n\n"
        return prefix + result.digest.markdown

    def _build_news_status_text(self) -> str:
        ensure_dirs()
        state = load_news_state()
        today = dt.date.today().isoformat()
        cached = load_cached_digest(date_iso=today)

        lines = [
            "News status",
            f"state_file: {news_state_path()}",
            f"last_run: {state.get('last_run')}",
            f"run_status: {state.get('status')}",
            f"timestamp: {state.get('timestamp')}",
            f"today_cache: {'hit' if cached else 'miss'} ({today})",
        ]
        if cached is not None:
            lines.append(
                f"cache_meta: items={cached.item_count}, fallback={cached.used_fallback}"
            )
        return "\n".join(lines)

    def _send_text(self, chat_id: int, text: str) -> None:
        self._bot.send_text(chat_id, text, timeout=15, limit=MAX_TELEGRAM_MESSAGE_LEN)

    def _send_text_and_record(self, chat_id: int, user_text: str, response_text: str) -> None:
        self._send_text(chat_id, response_text)
        self._emit_activity(f"replied to {chat_id}: {self._preview_text(response_text)}")
        try:
            save_exchange(self._history_session_id(chat_id), user_text, response_text)
        except Exception:
            pass

    def _emit_activity(self, message: str) -> None:
        sink = self._activity_sink
        if not callable(sink):
            return
        try:
            sink(ActivityEvent(source="telegram", message=message))
        except Exception:
            pass

    def handle_ux_event(self, event: UXEvent) -> None:
        """Receive a cross-surface UXEvent and broadcast it to all active Telegram chats."""
        text = event.render_text()
        if not text:
            return
        for chat_id in list(self._agents):
            try:
                self._send_text(chat_id, text)
            except Exception:
                pass

    def wire_hook_bus(self, hook_bus) -> None:
        """Subscribe to research/job UX events on a HookBus for cross-surface visibility."""
        if hook_bus is None or not hasattr(hook_bus, "register"):
            return
        hook_bus.register("ux.job_progress", self._on_hook_job_event)
        hook_bus.register("ux.job_completed", self._on_hook_job_completed)
        hook_bus.register("ux.tool_event", self._on_hook_tool_event)

    def _on_hook_job_event(self, hook_event) -> None:
        payload = getattr(hook_event, "payload", None) or {}
        event = payload.get("event")
        if isinstance(event, UXEvent):
            self.handle_ux_event(event)

    def _on_hook_job_completed(self, hook_event) -> None:
        self._on_hook_job_event(hook_event)

    def _on_hook_tool_event(self, hook_event) -> None:
        payload = getattr(hook_event, "payload", None) or {}
        event = payload.get("event")
        if not isinstance(event, UXEvent):
            return
        session_id = str(getattr(event, "session_id", "") or event.data.get("session_id", "")).strip()
        if not session_id:
            return
        chat_id = self._session_to_chat.get(session_id)
        if chat_id is None:
            return
        if event.kind == "tool_running" and event.data.get("detail_type") == "output_line":
            self._get_or_create_batch_collector(chat_id).add_line(str(event.data.get("line", "") or ""))
            return
        if event.kind == "tool_end":
            collector = self._batch_collectors.pop(chat_id, None)
            if collector is not None:
                collector.flush()
        text = self._telegram_renderer.format_event(event, status=str(payload.get("status", "") or ""))
        if not text:
            return
        try:
            self._send_text(chat_id, text)
        except Exception:
            pass

    def _get_or_create_batch_collector(self, chat_id: int) -> OutputBatchCollector:
        collector = self._batch_collectors.get(chat_id)
        if collector is None:
            collector = OutputBatchCollector(flush_fn=lambda text, _chat_id=chat_id: self._send_text(_chat_id, text))
            self._batch_collectors[chat_id] = collector
        return collector

    def _preview_text(self, text: str, limit: int = 80) -> str:
        compact = sanitize_terminal_notice_text(text)
        if len(compact) <= limit:
            return compact
        return compact[: max(0, limit - 3)] + "..."

    def _history_session_id(self, chat_id: int) -> str:
        sid = self._history_session_ids.get(chat_id)
        if sid:
            return sid
        sid = f"tg-{chat_id}-{new_session_id()}"
        self._history_session_ids[chat_id] = sid
        return sid

    def _api_call(self, method: str, payload: dict, timeout: int) -> dict:
        return self._bot.api_call(method, payload, timeout=timeout)


def _is_transient_get_updates_error(error: Exception) -> bool:
    text = str(error or "").strip().lower()
    if "telegram api getupdates" not in text:
        return False
    transient_markers = (
        "network error",
        "connection reset by peer",
        "remote end closed connection",
        "timed out",
        "temporarily unavailable",
        "connection aborted",
    )
    return any(marker in text for marker in transient_markers)


def _is_conflicting_get_updates_error(error: Exception) -> bool:
    text = str(error or "").strip().lower()
    if "telegram api getupdates" not in text:
        return False
    return "http 409" in text and "conflict" in text
