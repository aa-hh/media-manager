"""
Fetch tracker account statistics (ratio, upload, download, seeding count).

Blutopia uses the UNIT3D REST API (/api/user with Bearer token).
Beyond-HD uses a proprietary search API (no account-stats endpoint).
PrivateHD (AvistaZ) authenticates via /api/v1/jackett/auth.
"""
import json
from pathlib import Path

import requests as _requests


def _read_env(config_dir: Path) -> dict:
    env_path = config_dir / ".env"
    if not env_path.exists():
        return {}
    result = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _verify_ssl(cfg: dict) -> bool:
    return cfg.get("VERIFY_SSL", "true").lower() != "false"


def _unit3d_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _fetch_unit3d(base_url: str, username: str, api_key: str, verify: bool) -> dict:
    """Fetch account stats from a UNIT3D tracker REST API."""
    url = base_url.rstrip("/") + "/api/user"
    resp = _requests.get(
        url,
        headers=_unit3d_headers(api_key),
        timeout=15,
        verify=verify,
        allow_redirects=False,
    )
    if resp.is_redirect:
        raise ValueError("Authentication failed — invalid API key (redirected to login)")
    resp.raise_for_status()
    data = resp.json()
    ratio_raw = data.get("ratio")
    ratio = float(ratio_raw) if ratio_raw is not None else None
    return {
        "username": data.get("username") or username,
        "ratio": round(ratio, 4) if ratio is not None else None,
        "seeding": data.get("seeding"),
        "uploaded_bytes": None,
        "downloaded_bytes": None,
        "upload_gb": None,
        "download_gb": None,
    }


def _fetch_beyondhd(base_url: str, username: str, api_key: str, rss_key: str, verify: bool) -> dict:
    """Validate Beyond-HD API key via POST api/torrents/{apiKey}."""
    if not rss_key:
        raise ValueError("RSS key required — add it in Settings → Services")
    url = base_url.rstrip("/") + f"/api/torrents/{api_key}"
    resp = _requests.post(
        url,
        json={"action": "search", "rsskey": rss_key},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=15,
        verify=verify,
        allow_redirects=False,
    )
    if resp.is_redirect:
        raise ValueError("Invalid API key — redirected to login")
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("success") is False or payload.get("status_code") == 0:
        msg = payload.get("status_message") or payload.get("message") or "Invalid API key"
        raise ValueError(msg)
    return {
        "username": username or None,
        "ratio": None,
        "seeding": None,
        "uploaded_bytes": None,
        "downloaded_bytes": None,
        "upload_gb": None,
        "download_gb": None,
    }


def _fetch_avistaz(base_url: str, username: str, password: str, pid: str, verify: bool) -> dict:
    """Fetch account stats from an AvistaZ tracker (PrivateHD) via Jackett auth endpoint."""
    auth_resp = _requests.post(
        base_url.rstrip("/") + "/api/v1/jackett/auth",
        data={"username": username, "password": password, "pid": pid},
        headers={"Accept": "application/json"},
        timeout=15,
        verify=verify,
        allow_redirects=False,
    )
    if auth_resp.is_redirect:
        raise ValueError("Authentication failed — credentials rejected (redirect to login page)")
    if auth_resp.status_code in (401, 403):
        try:
            msg = auth_resp.json().get("message")
        except Exception:
            msg = None
        raise ValueError(msg or "Authentication failed — check username, password, and PID")
    auth_resp.raise_for_status()
    token = auth_resp.json().get("token")
    if not token:
        raise ValueError("No token returned from auth endpoint")
    bearer = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    me_resp = _requests.get(
        base_url.rstrip("/") + "/api/v1/jackett/torrents",
        headers=bearer,
        params={"limit": 1},
        timeout=15,
        verify=verify,
        allow_redirects=False,
    )
    if me_resp.is_redirect:
        raise ValueError("Token rejected — check account rank (Member+ required for API)")
    if me_resp.status_code in (401, 403):
        try:
            msg = me_resp.json().get("message")
        except Exception:
            msg = None
        raise ValueError(msg or "Token rejected — check account rank (Member+ required for API)")
    me_resp.raise_for_status()
    return {
        "username": username,
        "uploaded_bytes": None,
        "downloaded_bytes": None,
        "ratio": None,
        "seeding": None,
        "upload_gb": None,
        "download_gb": None,
    }


def fetch(config_dir: Path) -> dict:
    """
    Fetch account data for all configured trackers.
    Returns {tracker_key: account_dict} — keys match hostname substrings in tracker_rules.json.
    """
    cfg = _read_env(config_dir)
    verify = _verify_ssl(cfg)
    accounts: dict = {}

    # Blutopia (UNIT3D)
    bt_url  = cfg.get("TRACKER_BLUTOPIA_URL", "https://blutopia.cc")
    bt_user = cfg.get("TRACKER_BLUTOPIA_USERNAME", "")
    bt_key  = cfg.get("TRACKER_BLUTOPIA_API_KEY", "")
    if bt_key:
        try:
            accounts["blutopia.cc"] = _fetch_unit3d(bt_url, bt_user, bt_key, verify)
        except Exception as e:
            accounts["blutopia.cc"] = {"username": bt_user, "error": str(e)}

    # Beyond-HD (proprietary API — validates key only, no ratio endpoint)
    bhd_url  = cfg.get("TRACKER_BEYONDHD_URL", "https://beyond-hd.me")
    bhd_user = cfg.get("TRACKER_BEYONDHD_USERNAME", "")
    bhd_key  = cfg.get("TRACKER_BEYONDHD_API_KEY", "")
    bhd_rss  = cfg.get("TRACKER_BEYONDHD_RSS_KEY", "")
    if bhd_key:
        try:
            accounts["beyond-hd.me"] = _fetch_beyondhd(bhd_url, bhd_user, bhd_key, bhd_rss, verify)
        except Exception as e:
            accounts["beyond-hd.me"] = {"username": bhd_user, "error": str(e)}

    # PrivateHD (AvistaZ) — auth via /api/v1/jackett/auth
    phd_url  = cfg.get("TRACKER_PRIVATEHD_URL", "https://privatehd.to")
    phd_user = cfg.get("TRACKER_PRIVATEHD_USERNAME", "")
    phd_pass = cfg.get("TRACKER_PRIVATEHD_PASSWORD", "")
    phd_pid  = cfg.get("TRACKER_PRIVATEHD_PID", "")
    if phd_user:
        if phd_pass and phd_pid:
            try:
                accounts["privatehd.to"] = _fetch_avistaz(phd_url, phd_user, phd_pass, phd_pid, verify)
            except Exception as e:
                accounts["privatehd.to"] = {"username": phd_user, "error": str(e)}
        else:
            accounts["privatehd.to"] = {
                "username": phd_user,
                "error": "Password and PID required — add them in Settings → Services",
            }

    return accounts
