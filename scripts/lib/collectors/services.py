"""
Services health + version collector.
Checks each linked service's API, current version, and available updates.
Services that are not configured (no URL/key) are skipped gracefully.
"""
import requests
from datetime import datetime, timezone

from ..config import get as cfg, verify_ssl
from .. import log


def _get(url, headers=None, params=None, timeout=8):
    try:
        r = requests.get(url, headers=headers or {}, params=params or {},
                        timeout=timeout, verify=verify_ssl())
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _get_external(url, headers=None, params=None, timeout=8):
    """For external internet APIs — always verify SSL."""
    try:
        r = requests.get(url, headers=headers or {}, params=params or {},
                        timeout=timeout, verify=True)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _github_latest(repo: str) -> tuple[str | None, dict]:
    """Return (latest_version, changes) from GitHub releases."""
    data = _get_external(f"https://api.github.com/repos/{repo}/releases/latest",
                headers={"Accept": "application/vnd.github+json"}, timeout=10)
    if not data:
        return None, {}
    version = data.get("tag_name", "").lstrip("v")
    changes = _parse_github_body(data.get("body", ""))
    return version, changes


def _parse_github_body(body: str) -> dict:
    """Parse GitHub release markdown into {new: [...], fixed: [...]}."""
    import re
    new_items, fixed_items = [], []
    current = None
    for line in body.splitlines():
        low = line.lower()
        if re.search(r"feature|added|new|enhancements?", low) and line.startswith("#"):
            current = "new"
        elif re.search(r"fix|bug|patch|change|breaking", low) and line.startswith("#"):
            current = "fixed"
        elif line.strip().startswith("-") and current:
            text = re.sub(r"\s*-\s*\(\[.*", "", line.strip().lstrip("- "))
            text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
            text = re.sub(r"^\*[^*]+\*\s*", "", text)
            text = text.strip()
            if text and len(text) > 4:
                (new_items if current == "new" else fixed_items).append(text)
    return {"new": new_items[:6], "fixed": fixed_items[:6]}


def _check_arr(name: str, url: str, key: str) -> dict:
    if not url or not key:
        return {"name": name, "url": url or "", "reachable": False, "not_configured": True}

    params = {"apikey": key}
    status = _get(f"{url}/api/v3/system/status", params=params)
    if not status:
        return {"name": name, "url": url, "reachable": False}

    current = status.get("version", "unknown")
    updates = _get(f"{url}/api/v3/update", params=params) or []
    latest_entry = next((u for u in updates if u.get("latest")), None)
    latest = latest_entry["version"] if latest_entry else current
    installable = latest_entry.get("installable", False) if latest_entry else False
    update_available = latest_entry is not None and not latest_entry.get("installed", False)
    changes = latest_entry.get("changes", {}) if latest_entry else {}

    return {
        "name": name,
        "url": url,
        "reachable": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "installable": installable,
        "changes": changes,
        "update_method": "api",
        "trigger_cmd": (
            f"curl -sf -X POST '{url}/api/v3/command' "
            f"-H 'X-Api-Key: {key}' -H 'Content-Type: application/json' "
            f"-d '{{\"name\":\"ApplicationUpdate\"}}'"
        ),
    }


def _check_sonarr_instances() -> list[dict]:
    urls = [u.strip() for u in cfg("SONARR_URL", "").split(",") if u.strip()]
    keys = [k.strip() for k in cfg("SONARR_API_KEY", "").split(",") if k.strip()]
    if not urls:
        return [{"name": "Sonarr", "url": "", "reachable": False, "not_configured": True}]
    results = []
    for i, (url, key) in enumerate(zip(urls, keys + [""] * len(urls))):
        label = "Sonarr" if len(urls) == 1 else f"Sonarr ({i + 1})"
        results.append(_check_arr(label, url, key))
    return results


def _check_radarr_instances() -> list[dict]:
    urls = [u.strip() for u in cfg("RADARR_URL", "").split(",") if u.strip()]
    keys = [k.strip() for k in cfg("RADARR_API_KEY", "").split(",") if k.strip()]
    if not urls:
        return [{"name": "Radarr", "url": "", "reachable": False, "not_configured": True}]
    results = []
    for i, (url, key) in enumerate(zip(urls, keys + [""] * len(urls))):
        label = "Radarr" if len(urls) == 1 else f"Radarr ({i + 1})"
        results.append(_check_arr(label, url, key))
    return results


def _check_overseerr() -> dict:
    base = cfg("SEERR_URL", "")
    key = cfg("SEERR_API_KEY", "")
    if not base or not key:
        return {"name": "Overseerr", "url": base, "reachable": False, "not_configured": True}

    status = _get(f"{base}/api/v1/status", headers={"X-Api-Key": key})
    if not status:
        return {"name": "Overseerr", "url": base, "reachable": False}

    current = status.get("version", "unknown")
    update_available = status.get("updateAvailable", False)
    commits_behind = status.get("commitsBehind", 0)
    commit_tag = status.get("commitTag", "")

    latest, gh_changes = _github_latest("seerr-team/seerr")
    latest = latest or current

    def _ver_tuple(v):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except Exception:
            return (0,)

    update_available = update_available or (_ver_tuple(latest) > _ver_tuple(current))

    return {
        "name": "Overseerr",
        "url": base,
        "reachable": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "installable": False,
        "changes": gh_changes,
        "commits_behind": commits_behind,
        "commit_tag": commit_tag,
        "update_method": "manual",
    }


def _check_tautulli() -> dict:
    base = cfg("TAUTULLI_URL", "")
    key = cfg("TAUTULLI_API_KEY", "")
    if not base or not key:
        return {"name": "Tautulli", "url": base, "reachable": False, "not_configured": True}

    params = {"apikey": key, "cmd": "get_tautulli_info"}
    data = _get(f"{base}/api/v2", params=params)
    if not data or data.get("response", {}).get("result") != "success":
        return {"name": "Tautulli", "url": base, "reachable": False}

    info = data["response"]["data"]
    current = info.get("tautulli_version", "unknown").lstrip("v")
    install_type = info.get("tautulli_install_type", "unknown")

    latest, _ = _github_latest("Tautulli/Tautulli")
    latest = latest or current

    def _ver_tuple(v):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except Exception:
            return (0,)

    update_available = _ver_tuple(latest) > _ver_tuple(current)

    return {
        "name": "Tautulli",
        "url": base,
        "reachable": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": update_available,
        "installable": False,
        "install_type": install_type,
        "update_method": "manual",
        "update_notes": "Update via your deployment method.",
    }


def _check_plex() -> dict:
    token = cfg("PLEX_TOKEN", "")
    plex_url = cfg("PLEX_URL", "")
    if not plex_url or not token:
        return {"name": "Plex", "url": plex_url, "reachable": False, "not_configured": True}

    headers = {"X-Plex-Token": token, "Accept": "application/json"}
    data = _get(f"{plex_url}/identity", headers=headers)
    if not data:
        return {"name": "Plex", "url": plex_url, "reachable": False}

    mc = data.get("MediaContainer", data)
    current = mc.get("version", "unknown")

    return {
        "name": "Plex",
        "url": plex_url,
        "reachable": True,
        "current_version": current,
        "latest_version": None,
        "update_available": None,
        "installable": False,
        "update_method": "manual",
        "update_notes": "Update via Plex Web → Settings → Troubleshooting, or your server's package manager.",
    }


def _check_tmdb() -> dict:
    key = cfg("TMDB_API_KEY", "")
    if not key:
        return {"name": "TMDB", "url": "https://www.themoviedb.org", "reachable": False, "not_configured": True}

    data = _get_external("https://api.themoviedb.org/3/configuration", params={"api_key": key}, timeout=6)
    return {
        "name": "TMDB",
        "url": "https://www.themoviedb.org",
        "reachable": data is not None,
        "current_version": "API v3",
        "latest_version": None,
        "update_available": False,
        "installable": False,
        "update_method": None,
        "update_notes": "External API — no versioning.",
    }


def collect() -> dict:
    log.info("Checking linked services...")
    services = []

    for result in _check_sonarr_instances() + _check_radarr_instances():
        services.append(result)

    for fn in [_check_overseerr, _check_tautulli, _check_plex, _check_tmdb]:
        try:
            services.append(fn())
        except Exception as e:
            log.warning(f"Service check failed for {fn.__name__}: {e}")
            services.append({"name": fn.__name__.replace("_check_", "").title(), "reachable": False})

    configured = [s for s in services if not s.get("not_configured")]
    reachable = sum(1 for s in configured if s.get("reachable"))
    updates = sum(1 for s in configured if s.get("update_available"))
    log.info(f"Services: {reachable}/{len(configured)} reachable, {updates} update(s) available")

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": services,
    }
