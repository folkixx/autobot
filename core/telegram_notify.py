"""Telegram notifications — alerts admin when bot needs human help.

Also supports RECEIVING replies (manual-control mode): the bot asks a question
in TG and blocks until the admin answers with a normal message."""
import urllib.request
import urllib.parse
import json
import base64
import time
import ssl
import threading
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Tracks the last Telegram update we've consumed, so we only read NEW replies.
_offset_lock = threading.Lock()
_last_update_id = 0

# Some hosts (e.g. RU networks with DPI) MITM api.telegram.org with a
# self-signed cert, which makes urllib reject the connection
# (CERTIFICATE_VERIFY_FAILED). We disable verification for Telegram calls so
# notifications still work. It's our own bot on a controlled host.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _api(method: str, data: dict) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a text message to the admin chat."""
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "PASTE_CHAT_ID_HERE":
        return False
    body = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    result = _api("sendMessage", body)
    if not result.get("ok", False):
        print(f"[TG] sendMessage failed: {result}")
    return result.get("ok", False)


def send_screenshot(image_bytes: bytes, caption: str = "") -> bool:
    """Send a screenshot to the admin chat."""
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "PASTE_CHAT_ID_HERE":
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    boundary = "AutoBotBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{TELEGRAM_CHAT_ID}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception:
        return False


def notify(text: str, screenshot: bytes | None = None) -> None:
    """Non-blocking: send alert + optional screenshot in background thread."""
    def _send():
        msg = f"🤖 <b>AutoBot</b>\n\n{text}"
        send_message(msg)
        if screenshot:
            send_screenshot(screenshot, caption="Скриншот момента")
    threading.Thread(target=_send, daemon=True).start()


# ── Receiving replies (manual-control mode) ───────────────────────────────────

def _get_updates(offset: int, timeout: int = 25) -> list:
    """Long-poll Telegram for new updates."""
    url = (f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
           f"?offset={offset}&timeout={timeout}")
    try:
        with urllib.request.urlopen(url, timeout=timeout + 10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        return data.get("result", []) if data.get("ok") else []
    except Exception:
        return []


# ── Background listener (single poller feeds one inbox) ───────────────────────
# One thread polls getUpdates and pushes every admin message into _inbox.
# Both ask_human (blocking wait) and proactive interrupts (non-blocking drain)
# consume from this single inbox, so they never steal each other's updates.
import queue as _queue
_inbox: "_queue.Queue[str]" = _queue.Queue()
_poller_thread = None
_poller_stop = False


def _poll_loop(stop_flag):
    global _last_update_id
    # Skip messages that arrived before we started listening
    pre = _get_updates(0, timeout=0)
    if pre:
        with _offset_lock:
            _last_update_id = max(u["update_id"] for u in pre)
    while not _poller_stop and not (stop_flag and stop_flag()):
        with _offset_lock:
            offset = _last_update_id + 1
        for u in _get_updates(offset, timeout=20):
            with _offset_lock:
                _last_update_id = max(_last_update_id, u["update_id"])
            m = u.get("message") or u.get("edited_message") or {}
            chat_id = str(m.get("chat", {}).get("id", ""))
            text = (m.get("text") or "").strip()
            if text and (not TELEGRAM_CHAT_ID or chat_id == str(TELEGRAM_CHAT_ID)):
                _inbox.put(text)


def start_listener(stop_flag=None) -> None:
    """Start the background Telegram poller (idempotent)."""
    global _poller_thread, _poller_stop
    if _poller_thread and _poller_thread.is_alive():
        return
    _poller_stop = False
    _poller_thread = threading.Thread(target=_poll_loop, args=(stop_flag,), daemon=True)
    _poller_thread.start()


def stop_listener() -> None:
    global _poller_stop
    _poller_stop = True


def clear_inbox() -> None:
    """Drop any queued messages (call before asking, for a clean answer)."""
    try:
        while True:
            _inbox.get_nowait()
    except _queue.Empty:
        pass


def next_message() -> str | None:
    """Non-blocking: return the next pending admin message, or None."""
    try:
        return _inbox.get_nowait()
    except _queue.Empty:
        return None


def wait_for_reply(stop_flag=None, timeout: float = 1800.0) -> str | None:
    """Block until the admin sends a message (via the background poller)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_flag and stop_flag():
            return None
        try:
            return _inbox.get(timeout=1.0)
        except _queue.Empty:
            continue
    return None


def ask(question: str, screenshot: bytes | None = None,
        stop_flag=None, timeout: float = 1800.0, on_log=None) -> str | None:
    """Send a question to the admin and BLOCK until they reply."""
    import html
    start_listener(stop_flag)      # ensure the poller is running
    clear_inbox()                  # only accept a reply sent AFTER the question
    safe_q = html.escape(question)
    sent = send_message(
        f"🤖 <b>AutoBot спрашивает:</b>\n\n{safe_q}\n\n<i>Ответь сообщением.</i>")
    if not sent:
        sent = send_message(f"AutoBot спрашивает: {question}", parse_mode="")
    if on_log:
        on_log(f"   TG question sent: {sent} (chat_id={TELEGRAM_CHAT_ID})")
    if screenshot:
        send_screenshot(screenshot, caption="Текущий экран")
    return wait_for_reply(stop_flag=stop_flag, timeout=timeout)
