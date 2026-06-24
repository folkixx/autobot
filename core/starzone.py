"""StarZone (StarVPN) proxy API client.

API base: https://api.starhome.io/v1/
Auth: email + auth_token in every POST body.
"""
import json
import urllib.request
import urllib.parse
from config import (
    STARZONE_API_URL, STARZONE_EMAIL, STARZONE_AUTH_TOKEN,
    STARZONE_SLOT, STARZONE_PROXY_HOST, STARZONE_PROXY_PORT,
)

# US state name → 2-letter abbreviation mapping
US_STATES = {
    "Alabama": "al", "Alaska": "ak", "Arizona": "az", "Arkansas": "ar",
    "California": "ca", "Colorado": "co", "Connecticut": "ct", "Delaware": "de",
    "Florida": "fl", "Georgia": "ga", "Hawaii": "hi", "Idaho": "id",
    "Illinois": "il", "Indiana": "in", "Iowa": "ia", "Kansas": "ks",
    "Kentucky": "ky", "Louisiana": "la", "Maine": "me", "Maryland": "md",
    "Massachusetts": "ma", "Michigan": "mi", "Minnesota": "mn", "Mississippi": "ms",
    "Missouri": "mo", "Montana": "mt", "Nebraska": "ne", "Nevada": "nv",
    "New Hampshire": "nh", "New Jersey": "nj", "New Mexico": "nm", "New York": "ny",
    "North Carolina": "nc", "North Dakota": "nd", "Ohio": "oh", "Oklahoma": "ok",
    "Oregon": "or", "Pennsylvania": "pa", "Rhode Island": "ri", "South Carolina": "sc",
    "South Dakota": "sd", "Tennessee": "tn", "Texas": "tx", "Utah": "ut",
    "Vermont": "vt", "Virginia": "va", "Washington": "wa", "West Virginia": "wv",
    "Wisconsin": "wi", "Wyoming": "wy",
}


def _base_body(command: str) -> dict:
    return {
        "device_type": "web",
        "device_id": "starvpn-dashboard",
        "app_version": "1.0.0",
        "email": STARZONE_EMAIL,
        "auth_token": STARZONE_AUTH_TOKEN,
        "custom": 1,
        "command": command,
    }


def _post(endpoint: str, body: dict) -> dict:
    url = STARZONE_API_URL.rstrip("/") + "/" + endpoint.lstrip("/")
    payload = json.dumps(body).encode()
    # StarZone dashboard sends JSON but with x-www-form-urlencoded content-type
    for ct in ("application/json", "application/x-www-form-urlencoded"):
        req = urllib.request.Request(
            url, data=payload,
            headers={
                "Content-Type": ct,
                "Origin": "https://www.starvpn.com",
                "Referer": "https://www.starvpn.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                continue
            return {"error": f"HTTP {e.code}: {e.read().decode()}"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "403 Forbidden — both content types rejected"}


def _get(endpoint: str) -> dict:
    url = STARZONE_API_URL.rstrip("/") + "/" + endpoint.lstrip("/")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


# ── Public API ────────────────────────────────────────────────

def get_us_states() -> list[str]:
    """Return list of available US state abbreviations from StarZone."""
    result = _get("get_ip_configuration_options")
    states = []
    if "error" not in result:
        # Parse StarZone config options response
        data = result.get("data", result.get("options", result))
        if isinstance(data, dict):
            us = data.get("us", data.get("US", {}))
            if isinstance(us, dict):
                states = list(us.keys())
            elif isinstance(us, list):
                states = us
        elif isinstance(data, list):
            states = [s.get("code", s.get("name", "")) for s in data if isinstance(s, dict)]

    # Fallback to known common states if API returns nothing useful
    if not states:
        states = list(US_STATES.values())
    return states


def set_state(state: str, timeinterval: str = "sticky") -> bool:
    """Set proxy to a US state. state = 2-letter code (e.g. 'tx') or full name."""
    # Normalise to lowercase 2-letter code
    code = state.lower().strip()
    if len(code) > 2:
        code = US_STATES.get(state.title(), code[:2])

    body = _base_body("update_ip_configuration")
    body.update({
        "port": STARZONE_SLOT,
        "ip_type": "Rotating IP",
        "country": "us",
        "region": code,
        "timeinterval": timeinterval,
    })
    result = _post("", body)
    return "error" not in result


def rotate_ip() -> bool:
    """Rotate to a fresh IP within the current config."""
    body = _base_body("ip_update_now")
    body.update({"port": STARZONE_SLOT, "ip_type": "Rotating IP"})
    result = _post("", body)
    return "error" not in result


def get_current_config() -> dict:
    """Get current proxy slot configuration."""
    body = _base_body("refresh_data")
    return _post("", body)


def get_proxy_credentials(on_log=None) -> dict:
    """Proxy connection details for the LS session.

    StarZone's SOCKS/HTTP proxy (proxy.starzone.io:51313) authenticates by
    IP WHITELIST — the dashboard Proxy section has only an endpoint + an
    'Authorized IPs' list, no user/pass. The vpnusername/vpnpassword in
    ip_types are for the OVPN/WireGuard VPN, NOT the proxy. So the proxy
    connection is sent with NO auth; the machine's public IP must be added to
    StarZone 'Authorized IPs' for it to work.

    Type is HTTP: SOCKS5 refused on this account/endpoint; HTTP works and is
    functionally equivalent for browser traffic (same exit IP, same geo)."""
    if on_log:
        on_log("   StarZone proxy = HTTP, IP-whitelist (no user/pass). "
               "Machine IP must be in 'Authorized IPs'.")
    return {
        "host": STARZONE_PROXY_HOST,
        "port": STARZONE_PROXY_PORT,
        "username": "",
        "password": "",
        "type": "http",
    }


def get_vpn_credentials(on_log=None) -> dict:
    """OVPN/WireGuard credentials (data.ip_types[slot].vpnusername/vpnpassword).
    Used only if the system-VPN path is chosen instead of the SOCKS proxy."""
    raw = get_current_config()
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    target = {}
    ip_types = data.get("ip_types") if isinstance(data, dict) else None
    if isinstance(ip_types, list):
        for s in ip_types:
            if isinstance(s, dict) and str(s.get("port", "")) == str(STARZONE_SLOT):
                target = s
                break
        if not target and ip_types and isinstance(ip_types[0], dict):
            target = ip_types[0]
    return {
        "username": target.get("vpnusername", ""),
        "password": target.get("vpnpassword", ""),
        "wg_ipv4": target.get("wg_ipv4", ""),
    }


def get_proxy_url() -> str:
    """SOCKS5 proxy URL for Linken Sphere session."""
    return f"socks5://{STARZONE_PROXY_HOST}:{STARZONE_PROXY_PORT}"


def get_proxy_dict() -> dict:
    """Proxy dict for requests library."""
    url = get_proxy_url()
    return {"http": url, "https": url}
