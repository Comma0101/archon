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
from archon.history import new_session_id, save_exchange
from archon.news.runner import get_or_build_news_digest
from archon.news.state import load_cached_digest, load_news_state, news_state_path
from archon.safety import Level
from archon.ux.events import ActivityEvent

if TYPE_CHECKING:
    from archon.agent import Agent


MAX_TELEGRAM_MESSAGE_LEN = DEFAULT_TELEGRAM_MESSAGE_LIMIT


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
                print(f"[telegram] Poll error: {type(e).__name__}: {e}", file=sys.stderr)
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
        self._startup_synced = True
        if self._offset is not None:
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
            print(
                f"[telegram] Skipped {len(result)} pending update(s) on startup",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[telegram] Startup sync skipped ({type(e).__name__}: {e})", file=sys.stderr)

    def _get_updates(self) -> list[dict]:
        payload: dict[str, object] = {
            "timeout": self.poll_timeout_sec,
            "allowed_updates": ["message", "callback_query"],
        }
        if self._offset is not None:
            payload["offset"] = self._offset
        data = self._api_call("getUpdates", payload, timeout=self.poll_timeout_sec + 10)
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
                "Archon is connected.\n"
                "Commands: /help, /reset, /jobs, /job <id>, /news, /news_status, /approve, /deny, /approvals\n"
                "Dangerous commands can be approved with inline buttons or /approve.",
            )
            return

        if cmd == "/help":
            self._send_text_and_record(
                chat_id,
                body,
                "Send a message to chat with Archon.\n"
                "/reset - clear this chat's agent history\n"
                "/status - show current model/profile/token summary\n"
                "/cost - show session token totals\n"
                "/doctor - show local runtime health checks\n"
                "/permissions - show active policy permissions\n"
                "/skills - inspect or select built-in skills\n"
                "/plugins - inspect native and MCP plugins\n"
                "/mcp - inspect MCP server status and config\n"
                "/profile - inspect or change the active policy profile\n"
                "/jobs - list recent jobs across workers and calls\n"
                "/job <id> - show one normalized job summary\n"
                "/news - fetch or reuse today's AI news digest\n"
                "/news refresh - force refresh today's digest\n"
                "/news_status - show daily news state/cache status\n"
                "/approve - approve and replay the latest pending dangerous request\n"
                "/deny - deny and clear the latest pending dangerous request\n"
                "/approve_next - allow the next dangerous tool action only\n"
                "/approvals - show approval mode status\n"
                "/approvals on|off - enable/disable dangerous tool approvals for this chat\n"
                "Note: FORBIDDEN actions are always blocked.",
            )
            return

        if cmd == "/reset":
            agent = self._agents.pop(chat_id, None)
            if agent is not None:
                agent.reset()
            self._history_session_ids.pop(chat_id, None)
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

        handled, msg = handle_jobs_command(None, body)
        if handled:
            self._send_text_and_record(chat_id, body, msg)
            return

        handled, msg = handle_job_command(None, body)
        if handled:
            self._send_text_and_record(chat_id, body, msg)
            return

        if cmd == "/approve_next":
            self._approve_next_tokens[chat_id] = self._approve_next_tokens.get(chat_id, 0) + 1
            self._send_text_and_record(
                chat_id,
                body,
                "Approved next dangerous action for this chat (one-time).",
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
        try:
            agent = self._get_or_create_chat_agent(chat_id)
        except Exception as exc:
            agent = self._build_local_shell_fallback_agent(body)
            if agent is None:
                self._send_text_and_record(chat_id, body, f"Local command unavailable: {exc}")
                return True
        for handler in (
            handle_status_command,
            handle_cost_command,
            handle_doctor_command,
            handle_permissions_command,
            handle_skills_command,
            handle_plugins_command,
            handle_mcp_command,
            handle_profile_command,
        ):
            handled, msg = handler(agent, body)
            if handled:
                self._send_text_and_record(chat_id, body, msg)
                return True
        return False

    def _build_local_shell_fallback_agent(self, body: str):
        raw = (body or "").strip()
        parts = raw.split()
        if not parts:
            return None
        cmd = parts[0].lower()
        sub = parts[1].lower() if len(parts) > 1 else ""

        # Only use fallback for read-only inspection commands.
        if cmd == "/skills" and sub not in {"", "list", "show", "status"}:
            return None
        if cmd == "/profile" and sub not in {"", "show", "status", "list"}:
            return None
        if cmd in {"/status", "/cost", "/doctor", "/permissions", "/plugins", "/mcp"} or (
            cmd == "/skills" and sub in {"", "list", "show", "status"}
        ) or (cmd == "/profile" and sub in {"", "show", "status", "list"}):
            cfg = load_config()
            llm_cfg = getattr(cfg, "llm", None)
            return SimpleNamespace(
                config=cfg,
                llm=SimpleNamespace(
                    provider=str(getattr(llm_cfg, "provider", "") or ""),
                    model=str(getattr(llm_cfg, "model", "") or ""),
                    api_key=str(getattr(llm_cfg, "api_key", "") or ""),
                ),
                policy_profile="default",
                total_input_tokens=0,
                total_output_tokens=0,
                history=[],
            )
        return None

    def _get_or_create_chat_agent(self, chat_id: int) -> "Agent":
        agent = self._agents.get(chat_id)
        if agent is None:
            agent = self.agent_factory()
            agent.log_label = f"telegram chat={chat_id}"
            self._wire_chat_confirmer(agent, chat_id)
            self._wire_chat_route_progress(agent, chat_id)
            self._agents[chat_id] = agent
        else:
            agent.log_label = f"telegram chat={chat_id}"
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

        reply_text = self._handle_chat_body(
            chat_id,
            user_id,
            transcript,
            history_user_text=f"[{kind}] {transcript}",
        )
        if isinstance(reply_text, str):
            self._send_voice_reply_audio(chat_id, reply_text)

    def _handle_chat_body(
        self,
        chat_id: int,
        user_id: int,
        body: str,
        *,
        history_user_text: str,
    ) -> None:
        agent = self._get_or_create_chat_agent(chat_id)

        # Wire typing indicator: fire on every LLM call and tool call
        agent.on_thinking = lambda: self._send_typing(chat_id)
        agent.on_tool_call = lambda name, args: self._send_typing(chat_id)

        try:
            self._send_typing(chat_id)
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
            turn_id = getattr(agent, "last_turn_id", "")
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
        except Exception as e:
            print(f"[telegram] Voice reply TTS error for chat {chat_id}: {type(e).__name__}: {e}", file=sys.stderr)

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
        if subcmd in {"on", "enable"}:
            self._approval_always_on_chats.add(chat_id)
            return (
                "Telegram dangerous-action approvals enabled for this chat. "
                "FORBIDDEN actions remain blocked."
            )
        if subcmd in {"off", "disable"}:
            self._approval_always_on_chats.discard(chat_id)
            self._approval_elevated_until.pop(chat_id, None)
            return "Telegram dangerous-action approvals disabled for this chat."
        if subcmd in {"once", "next"}:
            self._approve_next_tokens[chat_id] = self._approve_next_tokens.get(chat_id, 0) + 1
            return "Approved next dangerous action for this chat (one-time)."
        if subcmd in {"unlock"}:
            self._approval_elevated_until[chat_id] = time.time() + ELEVATED_APPROVAL_TTL_SEC
            return "Telegram dangerous-action approvals enabled for this chat for 15 minutes."
        if subcmd in {"lock"}:
            self._approval_elevated_until.pop(chat_id, None)
            self._approval_always_on_chats.discard(chat_id)
            return "Telegram approvals locked for this chat."

        mode = "on" if chat_id in self._approval_always_on_chats else "off"
        next_count = self._approve_next_tokens.get(chat_id, 0)
        elevated_until = self._approval_elevated_until.get(chat_id, 0.0)
        elevated_remaining = max(0, int(elevated_until - time.time())) if elevated_until else 0
        return (
            "Telegram approvals status\n"
            f"dangerous_mode: {mode}\n"
            f"elevated_ttl_sec: {elevated_remaining}\n"
            f"approve_next_tokens: {next_count}\n"
            "Commands: /approve, /deny, /approve_next, /approvals on|off"
        )

    def _is_chat_elevated(self, chat_id: int) -> bool:
        until = self._approval_elevated_until.get(chat_id)
        if until is None:
            return False
        if until <= time.time():
            self._approval_elevated_until.pop(chat_id, None)
            return False
        return True

    def _pending_is_expired(self, pending: dict) -> bool:
        expires_at = pending.get("expires_at")
        return isinstance(expires_at, (int, float)) and expires_at <= time.time()

    def _queue_pending_approval(self, chat_id: int, command: str) -> None:
        ctx = self._current_request_ctx.get(chat_id) or {}
        user_text = str(ctx.get("user_text") or "")
        if not user_text:
            self._send_text(
                chat_id,
                "Dangerous action blocked. Use /approve_next to allow one action, or /approvals on "
                "to allow dangerous actions in this chat.",
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
                "Dangerous action blocked. Use /approve to replay the blocked request, /deny to clear it, "
                "or /approvals on to allow dangerous actions in this chat.",
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
            return "No pending dangerous request to approve."
        if self._pending_is_expired(pending):
            pending["status"] = "expired"
            self._pending_approvals.pop(chat_id, None)
            return "Pending dangerous request expired."
        if pending.get("status") != "pending":
            return f"Pending request already {pending.get('status')}."
        self._update_approval_prompt(chat_id, pending, "Approved. Replaying request…")
        return self._replay_pending_request(chat_id, pending, elevated_ttl_sec=None)

    def _deny_pending_request(self, chat_id: int) -> str:
        pending = self._pending_approvals.get(chat_id)
        if not pending:
            return "No pending dangerous request to deny."
        pending["status"] = "denied"
        self._pending_approvals.pop(chat_id, None)
        self._update_approval_prompt(chat_id, pending, "Denied.")
        return "Denied pending dangerous request."

    def _replay_pending_request(self, chat_id: int, pending: dict, elevated_ttl_sec: int | None) -> str:
        user_text = str(pending.get("user_text") or "").strip()
        if not user_text:
            pending["status"] = "error"
            self._pending_approvals.pop(chat_id, None)
            return "Pending request is missing the original message."
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
        return "Approved. Replaying pending request."

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

    def _preview_text(self, text: str, limit: int = 80) -> str:
        compact = " ".join(str(text or "").split())
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
