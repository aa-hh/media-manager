"""
Fetch torrent status from rTorrent/ruTorrent via XML-RPC.

Uses system.multicall to retrieve data for a specific set of known infohashes
in a single HTTP round-trip — avoids dumping the entire client torrent list.
"""
import xmlrpc.client
import requests
from urllib.parse import urlparse
from datetime import datetime, timezone

from .. import log
from ..config import verify_ssl

_FIELDS = [
    "d.state",              # 0=stopped 1=started
    "d.is_active",          # 1 if actively transferring
    "d.complete",           # 1 if download finished
    "d.ratio",              # upload/download * 1000
    "d.timestamp.started",
    "d.timestamp.finished", # when download hit 100% — seed time starts here
    "d.size_bytes",         # total torrent size (used for size-based H&R formulas)
    "d.up.total",           # bytes uploaded
    "d.down.total",         # bytes downloaded
    "d.tracker_url",        # active tracker announce URL
]


def fetch(url: str, username: str, password: str, hashes: list[str]) -> list[dict]:
    """Fetch rTorrent data for the given infohashes only. One batched RPC call."""
    if not hashes:
        return []

    rpc_url = url.rstrip("/") + "/RPC2"
    auth    = (username, password) if username else None

    calls = []
    for h in hashes:
        for method in _FIELDS:
            calls.append({"methodName": method, "params": [h.upper()]})

    body = xmlrpc.client.dumps((calls,), methodname="system.multicall")
    try:
        resp = requests.post(
            rpc_url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
            auth=auth,
            timeout=60,
            verify=verify_ssl(),
        )
        resp.raise_for_status()
        raw, _  = xmlrpc.client.loads(resp.content)
        results = raw[0]
    except Exception as e:
        log.warn(f"ruTorrent: RPC call failed: {e}")
        return []

    n_fields = len(_FIELDS)
    items: list[dict] = []

    for i, h in enumerate(hashes):
        base = i * n_fields
        if base + n_fields > len(results):
            break
        try:
            vals: dict = {}
            ok = True
            for j, field in enumerate(_FIELDS):
                r = results[base + j]
                if isinstance(r, dict) and "faultCode" in r:
                    ok = False
                    break
                vals[field] = r[0] if isinstance(r, list) and r else None
            if not ok:
                continue

            state      = int(vals.get("d.state") or 0)
            active     = int(vals.get("d.is_active") or 0)
            complete   = int(vals.get("d.complete") or 0)
            ratio_r    = int(vals.get("d.ratio") or 0)
            ts_started = int(vals.get("d.timestamp.started") or 0)
            ts_done    = int(vals.get("d.timestamp.finished") or 0)
            size_bytes = int(vals.get("d.size_bytes") or 0)
            up         = int(vals.get("d.up.total") or 0)
            down       = int(vals.get("d.down.total") or 0)
            tracker_url = str(vals.get("d.tracker_url") or "")

            if state == 0:
                status = "stopped"
            elif state == 1 and active == 1 and complete == 1:
                status = "seeding"
            elif state == 1 and complete == 0:
                status = "downloading"
            else:
                status = "unknown"

            now      = datetime.now(timezone.utc)
            added_at = None
            age_days = None
            if ts_started > 0:
                dt_start = datetime.fromtimestamp(ts_started, tz=timezone.utc)
                added_at = dt_start.isoformat()
                age_days = (now - dt_start).days

            # Seed time counts from when download finished, not when it was added.
            # ts_done=0 means not yet finished (still downloading).
            seed_hours = None
            if complete == 1 and ts_done > 0:
                dt_done    = datetime.fromtimestamp(ts_done, tz=timezone.utc)
                seed_hours = round((now - dt_done).total_seconds() / 3600, 1)

            tracker = ""
            if tracker_url:
                try:
                    tracker = urlparse(tracker_url).hostname or ""
                except Exception:
                    pass

            items.append({
                "hash":             h.upper(),
                "status":           status,
                "is_active":        bool(active),
                "ratio":            round(ratio_r / 1000, 3),
                "added_at":         added_at,
                "age_days":         age_days,
                "seed_hours":       seed_hours,
                "torrent_size_gb":  round(size_bytes / (1024 ** 3), 3) if size_bytes else None,
                "up_gb":            round(up   / (1024 ** 3), 3),
                "down_gb":          round(down / (1024 ** 3), 3),
                "tracker_url":      tracker_url,
                "tracker":          tracker,
            })
        except Exception as e:
            log.warn(f"ruTorrent: error parsing hash {h}: {e}")

    log.info(f"ruTorrent: {len(items)} matched from {len(hashes)} hashes")
    return items
