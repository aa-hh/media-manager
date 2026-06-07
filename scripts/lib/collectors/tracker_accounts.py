"""
Fetch tracker account statistics (ratio, upload, download, seeding count).

Supports UNIT3D-based trackers (Blutopia, Beyond-HD) via their REST API.
PrivateHD (AvistaZ) has no public REST API — credentials are stored but
stats cannot be fetched automatically.
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


def _fetch_avistaz(base_url: str, username: str, password: str, pid: str, verify: bool) -> dict:
    """Fetch account stats from an AvistaZ tracker (PrivateHD) via Jackett auth endpoint."""
    auth_resp = _requests.post(
        base_url.rstrip("/") + "/api/v1/jackett/auth",
        data={"username": username, "password": password, "pid": pid},
        timeout=15,
        verify=verify,
    )
    auth_resp.raise_for_status()
    token = auth_resp.json().get("token")
    if not token:
        raise ValueError("No token returned from auth endpoint")
    me_resp = _requests.get(
        base_url.rstrip("/") + "/api/v1/users/me",
        params={"api_token": token},
        timeout=15,
        verify=verify,
    )
    me_resp.raise_for_status()
    data = me_resp.json().get("data", {})
    up = data.get("uploaded") or 0
    dn = data.get("downloaded") or 1
    return {
        "username": username,
        "uploaded_bytes": up,
        "downloaded_bytes": dn,
        "ratio": round(up / dn, 4) if dn else None,
        "seeding": data.get("seeding"),
        "upload_gb": round(up / 1e9, 2),
        "download_gb": round(dn / 1e9, 2),
    }


def _fetch_unit3d(base_url: str, username: str, api_key: str, verify: bool) -> dict:
    """Fetch account stats from a UNIT3D tracker REST API."""
    url = base_url.rstrip("/") + f"/api/user/{username}"
    resp = _requests.get(url, params={"api_token": api_key}, timeout=15, verify=verify)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    up = data.get("uploaded") or 0
    dn = data.get("downloaded") or 1
    return {
        "username": username,
        "uploaded_bytes": up,
        "downloaded_bytes": dn,
        "ratio": round(up / dn, 4) if dn else None,
        "seeding": data.get("seeding"),
        "upload_gb": round(up / 1e9, 2),
        "download_gb": round(dn / 1e9, 2),
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
        if bt_user:
            try:
                accounts["blutopia.cc"] = _fetch_unit3d(bt_url, bt_user, bt_key, verify)
            except Exception as e:
                accounts["blutopia.cc"] = {"username": bt_user, "error": str(e)}
        else:
            accounts["blutopia.cc"] = {"error": "Username not configured — add your Blutopia username to fetch ratio stats"}

    # Beyond-HD (UNIT3D)
    bhd_url  = cfg.get("TRACKER_BEYONDHD_URL", "https://beyond-hd.me")
    bhd_user = cfg.get("TRACKER_BEYONDHD_USERNAME", "")
    bhd_key  = cfg.get("TRACKER_BEYONDHD_API_KEY", "")
    if bhd_key:
        if bhd_user:
            try:
                accounts["beyond-hd.me"] = _fetch_unit3d(bhd_url, bhd_user, bhd_key, verify)
            except Exception as e:
                accounts["beyond-hd.me"] = {"username": bhd_user, "error": str(e)}
        else:
            accounts["beyond-hd.me"] = {"error": "Username not configured — add your Beyond-HD username to fetch ratio stats"}

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
