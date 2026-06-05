"""
Fetches per-user watch history from Tautulli, keyed by TMDB ID.

Returns a dict: { tmdb_id: { username: WatchStats } }
"""
import requests
from .. import log
from ..config import verify_ssl


def _api(url: str, api_key: str, cmd: str, **params) -> dict:
    base_params = {"apikey": api_key, "cmd": cmd}
    base_params.update(params)
    resp = requests.get(f"{url}/api/v2", params=base_params, timeout=30, verify=verify_ssl())
    resp.raise_for_status()
    data = resp.json()
    return data.get("response", {})


def _get_users(url: str, api_key: str) -> list[dict]:
    resp = _api(url, api_key, "get_users_table", length=100)
    return resp.get("data", {}).get("data", [])


def _get_user_history(url: str, api_key: str, user_id: int, section_id: int, length: int = 10000) -> list[dict]:
    resp = _api(url, api_key, "get_history",
                user_id=user_id, section_id=section_id,
                length=length, order_column="date", order_dir="desc")
    return resp.get("data", {}).get("data", [])


def _process_history(history: list[dict], media_type: str, fname: str, store: dict,
                     tv_season_watch: dict, transcode_store: dict) -> None:
    """Process history entries, updating watch store, season watch, and transcode stats."""
    if not history:
        return

    by_item: dict[str, dict] = {}
    for entry in history:
        if media_type == "tv":
            item_key = entry.get("grandparent_rating_key") or entry.get("rating_key")
            item_title = entry.get("grandparent_title") or entry.get("title")
            guid = entry.get("grandparent_guid", "")
        else:
            item_key = entry.get("rating_key")
            item_title = entry.get("title")
            guid = entry.get("guid", "")

        if not item_key:
            continue

        tmdb_id = None
        if "tmdb://" in guid:
            try:
                tmdb_id = int(guid.split("tmdb://")[1].split("?")[0])
            except (ValueError, IndexError):
                pass

        if item_key not in by_item:
            by_item[item_key] = {
                "tmdb_id": tmdb_id,
                "title": item_title,
                "plays": 0,
                "duration_seconds": 0,
                "last_watched": None,
                "entries": [],
                # transcode aggregates (across all users)
                "direct_plays": 0,
                "transcode_plays": 0,
                "copy_plays": 0,
                "total_watch_pct": 0.0,
                "watch_pct_count": 0,
            }

        rec = by_item[item_key]
        if tmdb_id and not rec["tmdb_id"]:
            rec["tmdb_id"] = tmdb_id
        rec["plays"] += 1
        rec["duration_seconds"] += entry.get("duration", 0)
        date = entry.get("date")
        if date:
            if not rec["last_watched"] or date > rec["last_watched"]:
                rec["last_watched"] = date
        rec["entries"].append(entry)

        decision = (entry.get("transcode_decision") or "").lower()
        if decision == "direct play":
            rec["direct_plays"] += 1
        elif decision == "transcode":
            rec["transcode_plays"] += 1
        elif decision == "copy":
            rec["copy_plays"] += 1

        ws = entry.get("watched_status")
        if ws is not None:
            try:
                rec["total_watch_pct"] += float(ws)
                rec["watch_pct_count"] += 1
            except (TypeError, ValueError):
                pass

    for item_key, rec in by_item.items():
        tid = rec["tmdb_id"]
        if not tid:
            continue
        if tid not in store:
            store[tid] = {}

        if media_type == "tv":
            unique_eps = len({
                (e.get("parent_media_index"), e.get("media_index"))
                for e in rec["entries"]
                if e.get("parent_media_index") and e.get("media_index")
            })
            completion_pct = None  # resolved during enrichment with Sonarr episode counts

            # Season-level aggregation
            by_season: dict[int, dict] = {}
            for e in rec["entries"]:
                snum = e.get("parent_media_index")
                epnum = e.get("media_index")
                date = e.get("date")
                if not snum:
                    continue
                snum = int(snum)
                if snum not in by_season:
                    by_season[snum] = {"plays": 0, "last_watched": None, "unique_episodes": set()}
                srec = by_season[snum]
                srec["plays"] += 1
                if date and (srec["last_watched"] is None or date > srec["last_watched"]):
                    srec["last_watched"] = date
                if epnum:
                    srec["unique_episodes"].add(epnum)

            if tid not in tv_season_watch:
                tv_season_watch[tid] = {}
            for snum, srec in by_season.items():
                if snum not in tv_season_watch[tid]:
                    tv_season_watch[tid][snum] = {}
                tv_season_watch[tid][snum][fname] = {
                    "plays": srec["plays"],
                    "unique_episodes_watched": len(srec["unique_episodes"]),
                    "last_watched": srec["last_watched"],
                }
        else:
            completion_pct = 100 if rec["plays"] >= 1 else 0

        store[tid][fname] = {
            "plays": rec["plays"],
            "duration_seconds": rec["duration_seconds"],
            "last_watched": rec["last_watched"],
            "completion_pct": completion_pct,
            "unique_episodes_watched": (
                len({(e.get("parent_media_index"), e.get("media_index"))
                     for e in rec["entries"]
                     if e.get("parent_media_index") and e.get("media_index")})
                if media_type == "tv" else None
            ),
        }

        # Aggregate transcode stats across all users for this item
        total = rec["plays"]
        existing = transcode_store.get(tid, {})
        transcode_store[tid] = {
            "direct":    existing.get("direct", 0)    + rec["direct_plays"],
            "transcode": existing.get("transcode", 0) + rec["transcode_plays"],
            "copy":      existing.get("copy", 0)      + rec["copy_plays"],
            "total":     existing.get("total", 0)     + total,
            "avg_watch_pct": (
                round((existing.get("_wsum", 0.0) + rec["total_watch_pct"]) /
                      (existing.get("_wcnt", 0)   + rec["watch_pct_count"]), 2)
                if (existing.get("_wcnt", 0) + rec["watch_pct_count"]) > 0 else None
            ),
            "_wsum": existing.get("_wsum", 0.0) + rec["total_watch_pct"],
            "_wcnt": existing.get("_wcnt", 0)   + rec["watch_pct_count"],
        }


def fetch(
    url: str,
    api_key: str,
    tv_section_ids: list[str] | None = None,
    movie_section_ids: list[str] | None = None,
) -> dict:
    """
    Returns:
      {
        "tv": { tmdb_id: { friendly_name: { plays, duration_seconds, last_watched, completion_pct } } },
        "movie": { ... },
        "users": [ { user_id, friendly_name } ]
      }
    """
    if tv_section_ids is None:
        tv_section_ids = ["1"]
    if movie_section_ids is None:
        movie_section_ids = ["2"]

    users = _get_users(url, api_key)
    real_users = [u for u in users if u.get("friendly_name") and u.get("user_id")]
    log.info(f"Tautulli: found {len(real_users)} users")

    tv_watch: dict[int, dict] = {}
    tv_season_watch: dict[int, dict] = {}
    movie_watch: dict[int, dict] = {}
    tv_transcode: dict[int, dict] = {}
    movie_transcode: dict[int, dict] = {}

    for user in real_users:
        uid = user["user_id"]
        fname = user["friendly_name"]

        for section_id in tv_section_ids:
            history = _get_user_history(url, api_key, uid, section_id)
            _process_history(history, "tv", fname, tv_watch, tv_season_watch, tv_transcode)

        for section_id in movie_section_ids:
            history = _get_user_history(url, api_key, uid, section_id)
            _process_history(history, "movie", fname, movie_watch, tv_season_watch, movie_transcode)

    log.info(f"Tautulli: processed watch data for {len(tv_watch)} shows, {len(movie_watch)} movies")

    # Strip internal accumulator keys before serialising
    def _clean(ts: dict) -> dict:
        return {tid: {k: v for k, v in stats.items() if not k.startswith("_")}
                for tid, stats in ts.items()}

    user_list = [{"user_id": u["user_id"], "friendly_name": u["friendly_name"]} for u in real_users]
    return {
        "tv":       tv_watch,
        "tv_seasons": tv_season_watch,
        "movie":    movie_watch,
        "users":    user_list,
        "transcode_stats": {
            "tv":    _clean(tv_transcode),
            "movie": _clean(movie_transcode),
        },
    }
