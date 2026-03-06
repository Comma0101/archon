"""Shared Telegram Bot API client helpers (stdlib only)."""

from __future__ import annotations

import json
import uuid
from urllib import error as urlerror
from urllib import request as urlrequest


DEFAULT_TELEGRAM_MESSAGE_LIMIT = 4000


def chunk_telegram_text(text: str, limit: int = DEFAULT_TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split Telegram text on newline boundaries where possible."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < 1:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


class TelegramBotClient:
    """Minimal Telegram Bot API client with shared error handling."""

    def __init__(self, token: str):
        self.token = (token or "").strip()
        if not self.token:
            raise ValueError("Telegram bot token is required")

    def api_call(self, method: str, payload: dict, timeout: int = 10) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urlerror.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API {method} HTTP {e.code}: {raw}") from e
        except urlerror.URLError as e:
            raise RuntimeError(f"Telegram API {method} network error: {e.reason}") from e

        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Telegram API {method} returned invalid JSON") from e

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API {method} error: {data}")
        return data

    def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        timeout: int = 15,
        limit: int = DEFAULT_TELEGRAM_MESSAGE_LIMIT,
        disable_web_page_preview: bool = True,
        reply_markup: dict | None = None,
    ) -> None:
        for chunk in chunk_telegram_text(text, limit=limit):
            self.send_message(
                chat_id,
                chunk,
                timeout=timeout,
                disable_web_page_preview=disable_web_page_preview,
                reply_markup=reply_markup,
            )

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        timeout: int = 15,
        disable_web_page_preview: bool = True,
        reply_markup: dict | None = None,
    ) -> dict:
        payload = {
            "chat_id": int(chat_id),
            "text": text,
            "disable_web_page_preview": bool(disable_web_page_preview),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        data = self.api_call("sendMessage", payload, timeout=timeout)
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def get_file(self, file_id: str, *, timeout: int = 10) -> dict:
        payload = {"file_id": str(file_id)}
        data = self.api_call("getFile", payload, timeout=timeout)
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    def download_file(self, file_path: str, *, timeout: int = 20) -> bytes:
        path = str(file_path or "").lstrip("/")
        if not path:
            raise ValueError("Telegram file_path is required")
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        req = urlrequest.Request(url, method="GET")
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urlerror.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram file download HTTP {e.code}: {raw}") from e
        except urlerror.URLError as e:
            raise RuntimeError(f"Telegram file download network error: {e.reason}") from e

    def send_document_bytes(
        self,
        chat_id: int,
        *,
        filename: str,
        data: bytes,
        caption: str | None = None,
        mime_type: str = "application/octet-stream",
        timeout: int = 20,
    ) -> dict:
        if not filename:
            raise ValueError("filename is required")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("document bytes are required")

        fields: list[tuple[str, str]] = [("chat_id", str(int(chat_id)))]
        if caption:
            fields.append(("caption", str(caption)))
        body, boundary = _build_multipart_form_data(
            fields=fields,
            file_field="document",
            filename=str(filename),
            file_bytes=bytes(data),
            mime_type=str(mime_type or "application/octet-stream"),
        )
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.token}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urlerror.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API sendDocument HTTP {e.code}: {err}") from e
        except urlerror.URLError as e:
            raise RuntimeError(f"Telegram API sendDocument network error: {e.reason}") from e

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError("Telegram API sendDocument returned invalid JSON") from e
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API sendDocument error: {payload}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def send_voice_bytes(
        self,
        chat_id: int,
        *,
        filename: str,
        data: bytes,
        caption: str | None = None,
        mime_type: str = "audio/ogg",
        timeout: int = 20,
    ) -> dict:
        if not filename:
            raise ValueError("filename is required")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("voice bytes are required")

        fields: list[tuple[str, str]] = [("chat_id", str(int(chat_id)))]
        if caption:
            fields.append(("caption", str(caption)))
        body, boundary = _build_multipart_form_data(
            fields=fields,
            file_field="voice",
            filename=str(filename),
            file_bytes=bytes(data),
            mime_type=str(mime_type or "audio/ogg"),
        )
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.token}/sendVoice",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urlerror.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API sendVoice HTTP {e.code}: {err}") from e
        except urlerror.URLError as e:
            raise RuntimeError(f"Telegram API sendVoice network error: {e.reason}") from e

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError("Telegram API sendVoice returned invalid JSON") from e
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API sendVoice error: {payload}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def send_typing(self, chat_id: int, *, timeout: int = 5) -> None:
        self.api_call("sendChatAction", {"chat_id": int(chat_id), "action": "typing"}, timeout=timeout)

    def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
        timeout: int = 5,
    ) -> None:
        payload: dict[str, object] = {
            "callback_query_id": str(callback_query_id),
            "show_alert": bool(show_alert),
        }
        if text:
            payload["text"] = text
        self.api_call("answerCallbackQuery", payload, timeout=timeout)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        timeout: int = 10,
        disable_web_page_preview: bool = True,
        reply_markup: dict | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "text": text,
            "disable_web_page_preview": bool(disable_web_page_preview),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.api_call("editMessageText", payload, timeout=timeout)


def _build_multipart_form_data(
    *,
    fields: list[tuple[str, str]],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
) -> tuple[bytes, str]:
    boundary = f"ArchonBoundary{uuid.uuid4().hex}"
    boundary_bytes = boundary.encode("ascii")
    body = bytearray()

    for name, value in fields:
        body.extend(b"--" + boundary_bytes + b"\r\n")
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(b"--" + boundary_bytes + b"\r\n")
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(
            "utf-8"
        )
    )
    body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(b"--" + boundary_bytes + b"--\r\n")
    return bytes(body), boundary
