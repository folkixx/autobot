"""Linken Sphere local API client.

Base URL: http://127.0.0.1:35000  (no auth token needed — local only)
Docs:     https://documenter.getpostman.com/view/32398185/2s9YsRd9cC

Session lifecycle:
  create_quick → uuid
  start(uuid)  → debug_port
  stop(uuid)
  remove(uuid)
"""
import json
import time
import urllib.request
import urllib.error
from config import LINKEN_SPHERE_URL, LINKEN_SPHERE_BROWSER


def _post(path: str, body: dict | None = None) -> dict:
    url = LINKEN_SPHERE_URL.rstrip("/") + "/" + path.lstrip("/")
    payload = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw.strip() else {"ok": True}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        return {"error": f"HTTP {e.code}: {body_text}"}
    except Exception as e:
        return {"error": str(e)}


def _get(path: str) -> dict:
    url = LINKEN_SPHERE_URL.rstrip("/") + "/" + path.lstrip("/")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw.strip() else {}
    except Exception as e:
        return {"error": str(e)}


# ── Session management ────────────────────────────────────────

def list_sessions() -> list[dict]:
    """Return all sessions."""
    result = _get("/sessions")
    if isinstance(result, list):
        return result
    return result.get("data", result.get("sessions", []))


def create_session(
    name: str = "autobot",
    proxy_host: str = "",
    proxy_port: int = 0,
    proxy_type: str = "socks5",
    proxy_login: str = "",
    proxy_password: str = "",
    on_log=None,
) -> str | None:
    """Create a new session with Hybrid 2.0 (mimic) browser and proxy.
    Returns session UUID or None on failure."""
    def _log(m):
        (on_log or print)(m)

    body: dict = {
        "name": name,
        "browser_type": LINKEN_SPHERE_BROWSER,  # mimic = Hybrid 2.0
    }

    if proxy_host and proxy_port:
        # Send proxy with redundant key aliases — different LS versions read
        # different names (type/protocol, host/ip). Extra keys are ignored.
        proxy = {
            "type": proxy_type,
            "protocol": proxy_type,
            "host": proxy_host,
            "ip": proxy_host,
            "port": int(proxy_port),
        }
        if proxy_login:
            proxy["login"] = proxy_login
            proxy["username"] = proxy_login
        if proxy_password:
            proxy["password"] = proxy_password
        body["proxy"] = proxy
        _log(f"   LS proxy body: {json.dumps(proxy)}")
    else:
        _log("   LS: NO proxy host/port provided → session will be Direct")

    for endpoint in ("/sessions/create_quick", "/sessions/create"):
        result = _post(endpoint, body)
        _log(f"   LS {endpoint} → {str(result)[:300]}")

        # LS sometimes returns a list of sessions
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    uid = item.get("uuid") or item.get("id")
                    if uid:
                        return uid
            continue

        if isinstance(result, dict):
            if "error" not in result:
                return result.get("uuid") or result.get("id")

    return None


def set_connection(uuid: str, ptype: str, ip: str, port: int,
                   login: str = "", password: str = "", on_log=None) -> dict:
    """Set the proxy/connection on a session via POST /sessions/connection.
    This is the CORRECT way — create_quick ignores inline proxy (it uses
    presets). Field name is `ip` (host works too). Returns the LS response."""
    body = {"uuid": uuid, "type": ptype, "ip": ip, "port": int(port)}
    if login:
        body["login"] = login
    if password:
        body["password"] = password
    result = _post("/sessions/connection", body)
    if on_log:
        on_log(f"   LS /sessions/connection {ptype}://{ip}:{port} → {str(result)[:250]}")
    return result


def check_proxy(uuid: str, on_log=None) -> dict:
    """Ask LS to test the session's proxy. Returns egress IP / status."""
    result = _post("/sessions/check_proxy", {"uuid": uuid})
    if on_log:
        on_log(f"   LS /sessions/check_proxy → {str(result)[:250]}")
    return result


def start_session(uuid: str, headless: bool = False, chromium_args=None) -> dict:
    """Start a session. Returns {debug_port, uuid} or {error}.
    chromium_args lets us open the window maximized from the start (consistent
    coordinates) instead of resizing after, which desynced clicks."""
    body = {"uuid": uuid, "headless": headless}
    if chromium_args:
        body["chromium_args"] = chromium_args
    return _post("/sessions/start", body)


def stop_session(uuid: str) -> bool:
    result = _post("/sessions/stop", {"uuid": uuid})
    return "error" not in result


def remove_session(uuid: str) -> bool:
    result = _post("/sessions/remove", {"uuid": uuid})
    return "error" not in result


# ── High-level launcher ───────────────────────────────────────

def launch_session(
    proxy_host: str = "",
    proxy_port: int = 0,
    name: str = "autobot",
    proxy_type: str = "socks5",
    proxy_login: str = "",
    proxy_password: str = "",
    on_log=None,
) -> dict:
    """
    Create + start a session in one call.
    Returns {uuid, debug_port} or {error}.
    """
    uuid = create_session(name=name, on_log=on_log)
    if not uuid:
        return {"error": "Failed to create LS session — check LS is running on port 35000"}

    # Set the proxy via the DEDICATED endpoint (create_quick ignores inline
    # proxy — it uses presets). Without this the session is Direct.
    if proxy_host and proxy_port:
        set_connection(uuid, proxy_type, proxy_host, proxy_port,
                       proxy_login, proxy_password, on_log=on_log)
        time.sleep(0.5)
        check_proxy(uuid, on_log=on_log)  # logs the real egress IP

    time.sleep(1.0)

    # Open the window maximized from the start — consistent coordinates.
    start = start_session(uuid, chromium_args=["--start-maximized"])

    # LS sometimes returns a list of session objects instead of a single dict
    if isinstance(start, list):
        # Find our session in the list
        match = next((s for s in start if isinstance(s, dict) and s.get("uuid") == uuid), None)
        start = match or (start[0] if start else {})

    if not isinstance(start, dict):
        remove_session(uuid)
        return {"error": f"Unexpected start_session response type: {type(start)} — {start}"}

    if "error" in start:
        remove_session(uuid)
        return start

    debug_port = (
        start.get("debug_port")
        or start.get("port")
        or start.get("remoteDebuggingPort")
        or start.get("debugPort")
    )
    if not debug_port:
        remove_session(uuid)
        return {"error": f"No debug_port in response: {start}"}

    return {"uuid": uuid, "debug_port": int(debug_port)}
