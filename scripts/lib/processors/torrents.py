"""
Matches ruTorrent data to library items via Sonarr/Radarr download history.
"""
import json
import math
import re
from pathlib import Path


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def _load_tracker_rules(config_dir: Path) -> dict:
    try:
        f = config_dir / "tracker_rules.json"
        if f.exists():
            return json.loads(f.read_text())
    except Exception:
        pass
    return {}


def _privatehd_required_hours(size_gb: float) -> float:
    """
    PrivateHD H&R formula (exact piecewise as published):
      x <= 1 GB  : 72 hours (flat)
      1 < x < 50 : 72 + 2*x hours
      x >= 50    : 100*ln(x) - 219.2023 hours
    """
    if size_gb <= 1:
        return 72.0
    if size_gb < 50:
        return 72.0 + 2.0 * size_gb
    return 100.0 * math.log(size_gb) - 219.2023


def _privatehd_buffer_hours(size_gb: float) -> float:
    """Grace period before PrivateHD officially marks a H&R: 1 day + 1h per 5GB."""
    return 24.0 + (size_gb / 5.0)


def _slug(s: str) -> str:
    """Normalize to alphanumeric lowercase, stripping TLD suffixes."""
    # Remove common TLDs so "beyond-hd.me" and "BeyondHD" both slug to "beyondhd"
    s = re.sub(r"\.(me|cc|to|com|net|org|io)\b", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def _find_rule(tracker: str, rules: dict) -> tuple[str, dict] | None:
    """Match tracker hostname or indexer name to a rule key.

    Accepts both exact hostnames (e.g. 'beyond-hd.me') and Prowlarr-style
    indexer names (e.g. 'BeyondHD (Prowlarr)') by comparing slugified forms.
    """
    if not tracker:
        return None
    tracker_slug = _slug(tracker)
    for key, rule in rules.items():
        if key.startswith("_"):
            continue
        # Direct substring match (original behaviour)
        if key in tracker or tracker in key:
            return key, rule
        # Slug-based match — handles "BeyondHD (Prowlarr)" ↔ "beyond-hd.me"
        key_slug = _slug(key)
        if len(key_slug) >= 5 and (key_slug in tracker_slug or tracker_slug in key_slug):
            return key, rule
    return None


def _indexer_to_tracker(indexer: str, rules: dict) -> str:
    """Resolve a Prowlarr/Jackett indexer name to the canonical tracker hostname
    used as a key in tracker_rules.json.  Returns '' if no match."""
    if not indexer:
        return ""
    match = _find_rule(indexer, rules)
    return match[0] if match else ""


def _check_requirements(torrent: dict, rules: dict) -> dict | None:
    """
    Evaluate tracker H&R requirements for a torrent.

    Rule keys:
      min_ratio        float  — minimum upload/download ratio
      min_hours        float  — minimum seed hours (starts after download completes)
      logic            str    — "and" (default) or "or" (either condition clears H&R)
      min_hours_formula str   — "privatehd" uses size-based formula instead of min_hours

    Returns a dict describing the check, or None if no rule applies.
    """
    match = _find_rule(torrent.get("tracker", ""), rules)
    if not match:
        return None
    _key, rule = match

    logic     = rule.get("logic", "and")
    ratio     = torrent.get("ratio", 0.0)
    seed_h    = torrent.get("seed_hours")        # None if download not complete
    size_gb   = torrent.get("torrent_size_gb")

    # Determine required hours
    min_hours: float | None = None
    hours_formula: str | None = None
    hnr_buffer_hours: float | None = None
    if rule.get("min_hours_formula") == "privatehd":
        hours_formula = "privatehd"
        if size_gb is not None:
            min_hours = round(_privatehd_required_hours(size_gb), 1)
            hnr_buffer_hours = round(_privatehd_buffer_hours(size_gb), 1)
    elif rule.get("min_hours") is not None:
        min_hours = float(rule["min_hours"])

    min_ratio: float | None = float(rule["min_ratio"]) if rule.get("min_ratio") is not None else None

    # Evaluate individual conditions
    hours_ok = (seed_h is not None and min_hours is not None and seed_h >= min_hours) if min_hours is not None else None
    ratio_ok = (ratio >= min_ratio) if min_ratio is not None else None

    # Overall met determination
    if logic == "or":
        if hours_ok is None and ratio_ok is None:
            met = None
        else:
            met = bool(hours_ok) or bool(ratio_ok)
    else:
        # "and": all defined conditions must pass
        conds = [c for c in (hours_ok, ratio_ok) if c is not None]
        met = all(conds) if conds else None

    return {
        "logic":            logic,
        "min_hours":        min_hours,
        "hours_formula":    hours_formula,
        "hnr_buffer_hours": hnr_buffer_hours,
        "min_ratio":        min_ratio,
        "seed_hours":       seed_h,
        "ratio":            ratio,
        "hours_ok":         hours_ok,
        "ratio_ok":         ratio_ok,
        "met":              met,
    }


_STATUS_RANK = {"seeding": 0, "stopped": 1, "downloading": 2, "unknown": 3}


def _get_account(tracker: str, accounts: dict) -> tuple[str, dict] | None:
    """Match tracker hostname to an account entry (substring match)."""
    if not tracker or not accounts:
        return None
    for key, acct in accounts.items():
        if key in tracker or tracker in key:
            return key, acct
    return None


def apply(data_dir: Path, config_dir: Path, shows: list[dict], movies: list[dict]) -> dict:
    """
    Load raw torrent + history data, match to library items, inject 'torrent' field.
    Returns the item_id → torrent_info map (also written to data/torrents.json).
    Also writes data/tracker_accounts.json with enriched account stats.
    """
    raw_torrents    = _load_json(data_dir / "raw_rutorrent.json",        [])
    sonarr_history  = _load_json(data_dir / "raw_sonarr_history.json",   [])
    radarr_history  = _load_json(data_dir / "raw_radarr_history.json",   [])
    raw_accounts    = _load_json(data_dir / "raw_tracker_accounts.json", {})
    tracker_rules   = _load_tracker_rules(config_dir)

    # hash → item_id  AND  hash → tracker hostname (from indexer name in history)
    hash_to_item:    dict[str, str] = {}
    hash_to_tracker: dict[str, str] = {}
    for entry in sonarr_history:
        dl = (entry.get("download_id") or "").upper().strip()
        sid = entry.get("sonarr_id")
        if dl and sid:
            hash_to_item[dl] = f"tv:{sid}"
            t = _indexer_to_tracker(entry.get("indexer", ""), tracker_rules)
            if t:
                hash_to_tracker[dl] = t
    for entry in radarr_history:
        dl = (entry.get("download_id") or "").upper().strip()
        rid = entry.get("radarr_id")
        if dl and rid:
            hash_to_item[dl] = f"movie:{rid}"
            t = _indexer_to_tracker(entry.get("indexer", ""), tracker_rules)
            if t:
                hash_to_tracker[dl] = t

    # Build item_id → best torrent (prefer seeding > stopped > others)
    torrent_map: dict[str, dict] = {}
    for t in raw_torrents:
        h       = (t.get("hash") or "").upper()
        item_id = hash_to_item.get(h)
        if not item_id:
            continue
        # If rTorrent didn't supply a tracker URL, fall back to the indexer
        # name recorded in Sonarr/Radarr history.
        if not t.get("tracker"):
            t = {**t, "tracker": hash_to_tracker.get(h, "")}
        req = _check_requirements(t, tracker_rules)

        # Layer in global ratio check from tracker account data
        if req is not None:
            match = _find_rule(t.get("tracker", ""), tracker_rules)
            if match:
                _, rule = match
                min_global = rule.get("min_global_ratio")
                if min_global is not None:
                    acct_match = _get_account(t.get("tracker", ""), raw_accounts)
                    acct = acct_match[1] if acct_match else None
                    global_ratio = acct.get("ratio") if acct else None
                    global_ok = (
                        global_ratio is not None
                        and not acct.get("error")
                        and global_ratio >= float(min_global)
                    ) if acct else None
                    req["global_ratio_ok"]   = global_ok
                    req["global_ratio"]      = global_ratio
                    req["min_global_ratio"]  = float(min_global)
                    # Global ratio failure overrides per-torrent met status
                    if global_ok is False:
                        req["met"] = False

        enriched = {
            **t,
            "requirements":     req,
            "requirements_met": req["met"] if req is not None else None,
        }
        existing = torrent_map.get(item_id)
        if not existing or _STATUS_RANK.get(enriched["status"], 99) < _STATUS_RANK.get(existing["status"], 99):
            torrent_map[item_id] = enriched

    for item in shows:
        item["torrent"] = torrent_map.get(item["id"])
    for item in movies:
        item["torrent"] = torrent_map.get(item["id"])

    # Count linked items per tracker
    tracker_linked: dict[str, int] = {}
    for t in torrent_map.values():
        host = t.get("tracker") or ""
        if host:
            tracker_linked[host] = tracker_linked.get(host, 0) + 1

    # Enrich tracker accounts with rule data + linked counts
    processed_accounts: dict = {}
    for key, acct in raw_accounts.items():
        rule_match = _find_rule(key, tracker_rules)
        min_global_ratio = float(rule_match[1]["min_global_ratio"]) if rule_match and rule_match[1].get("min_global_ratio") else None
        ratio = acct.get("ratio")
        if acct.get("error") or ratio is None:
            global_ok = None
        elif min_global_ratio is not None:
            global_ok = ratio >= min_global_ratio
        else:
            global_ok = None

        linked = sum(
            count for host, count in tracker_linked.items()
            if key in host or host in key
        )
        processed_accounts[key] = {
            **acct,
            "min_global_ratio": min_global_ratio,
            "global_ratio_ok":  global_ok,
            "linked_count":     linked,
        }

    (data_dir / "torrents.json").write_text(json.dumps(torrent_map, indent=2))
    (data_dir / "tracker_accounts.json").write_text(json.dumps(processed_accounts, indent=2))
    return torrent_map
