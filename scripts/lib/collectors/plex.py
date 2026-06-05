"""
Fetches per-user watch history from Plex directly.

Returns the same shape as tautulli.fetch() so it's a drop-in replacement:
  {
    "tv":    { tmdb_id: { friendly_name: WatchStats } },
    "movie": { tmdb_id: { friendly_name: WatchStats } },
    "users": [ { user_id, friendly_name } ]
  }
"""
import requests
from xml.etree import ElementTree as ET
from datetime import datetime
from .. import log
from ..config import verify_ssl

PAGE_SIZE = 1000


def _get(url: str, token: str, path: str, params: dict | None = None) -> ET.Element:
    p = {"X-Plex-Token": token}
    if params:
        p.update(params)
    ssl_verify = verify_ssl()
    if not ssl_verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    resp = requests.get(f"{url}{path}", params=p, timeout=60, verify=ssl_verify)
    resp.raise_for_status()
    return ET.fromstring(resp.content)


def _get_accounts(url: str, token: str) -> list[dict]:
    root = _get(url, token, "/accounts")
    accounts = []
    for acct in root:
        aid = acct.attrib.get("id")
        name = acct.attrib.get("name", "")
        if aid and aid != "0" and name:
            accounts.append({"id": int(aid), "name": name})
    return accounts


def _extract_tmdb_id(item_el: ET.Element) -> int | None:
    for guid in item_el.findall("Guid"):
        gid = guid.attrib.get("id", "")
        if gid.startswith("tmdb://"):
            try:
                return int(gid.split("tmdb://")[1].split("?")[0])
            except (ValueError, IndexError):
                pass
    return None


def _get_library_metadata(url: str, token: str, section_id: str) -> dict:
    """
    Returns { title: { ratingKey, tmdb_id, leaf_count } }.
    Uses includeGuids=1 on the /all endpoint to get TMDB IDs in a single
    request. Falls back to per-item requests for any that still lack one.
    """
    root = _get(url, token, f"/library/sections/{section_id}/all", {
        "X-Plex-Container-Start": "0",
        "X-Plex-Container-Size": "1000",
        "includeGuids": "1",
    })
    items = {}
    for item in root:
        title = item.attrib.get("title", "")
        rating_key = item.attrib.get("ratingKey")
        leaf_count = int(item.attrib.get("leafCount") or 0)
        if not title or not rating_key:
            continue
        items[title] = {
            "rating_key": rating_key,
            "tmdb_id": _extract_tmdb_id(item),
            "leaf_count": leaf_count,
        }

    # Fall back to individual metadata requests for anything still missing a TMDB ID
    missing = [t for t, m in items.items() if m["tmdb_id"] is None]
    if missing:
        log.info(f"Plex: fetching individual metadata for {len(missing)} items without TMDB GUID")
    for title in missing:
        meta = items[title]
        try:
            detail = _get(url, token, f"/library/metadata/{meta['rating_key']}")
            children = list(detail)
            if children:
                meta["tmdb_id"] = _extract_tmdb_id(children[0])
        except Exception:
            pass

    return items


def _fetch_history_page(
    url: str, token: str, account_id: int, section_id: str, start: int
) -> tuple[list[dict], int]:
    root = _get(url, token, "/status/sessions/history/all", {
        "accountID": str(account_id),
        "librarySectionID": section_id,
        "X-Plex-Container-Start": str(start),
        "X-Plex-Container-Size": str(PAGE_SIZE),
        "sort": "viewedAt:asc",
    })
    total = int(root.attrib.get("totalSize") or root.attrib.get("size") or 0)
    entries = [dict(item.attrib) for item in root]
    return entries, total


def _fetch_all_history(
    url: str, token: str, account_id: int, section_id: str
) -> list[dict]:
    all_entries = []
    start = 0
    _, total = _fetch_history_page(url, token, account_id, section_id, 0)
    if total == 0:
        return []
    while start < total:
        entries, _ = _fetch_history_page(url, token, account_id, section_id, start)
        all_entries.extend(entries)
        start += PAGE_SIZE
    return all_entries


def get_library_sections(url: str, token: str) -> list[dict]:
    """Returns [{id, title, type}] by calling /library/sections."""
    root = _get(url, token, "/library/sections")
    sections = []
    for directory in root:
        key = directory.attrib.get("key")
        title = directory.attrib.get("title", "")
        type_ = directory.attrib.get("type", "")
        if key:
            sections.append({"id": key, "title": title, "type": type_})
    return sections


def _get_machine_id(url: str, token: str) -> str:
    try:
        root = _get(url, token, "/identity")
        return root.attrib.get("machineIdentifier", "")
    except Exception:
        return ""


def fetch(
    url: str,
    token: str,
    name_map: dict | None = None,
    tv_section_ids: list[str] | None = None,
    movie_section_ids: list[str] | None = None,
) -> dict:
    if tv_section_ids is None:
        tv_section_ids = ["1"]
    if movie_section_ids is None:
        movie_section_ids = ["2"]

    machine_id = _get_machine_id(url, token)
    accounts = _get_accounts(url, token)
    log.info(f"Plex: found {len(accounts)} accounts")

    # Build title → metadata maps for matching (merge across all section IDs)
    show_meta: dict = {}
    for sid in tv_section_ids:
        show_meta.update(_get_library_metadata(url, token, sid))
    movie_meta: dict = {}
    for sid in movie_section_ids:
        movie_meta.update(_get_library_metadata(url, token, sid))
    log.info(f"Plex: indexed {len(show_meta)} shows, {len(movie_meta)} movies")

    tv_watch: dict[int, dict] = {}
    tv_season_watch: dict[int, dict] = {}  # { tmdb_id: { season_num: { user: WatchStats } } }
    movie_watch: dict[int, dict] = {}

    for account in accounts:
        aid = account["id"]
        raw_name = account["name"]
        fname = (name_map or {}).get(raw_name, raw_name)

        # TV history — aggregate across all TV section IDs
        tv_history = []
        for sid in tv_section_ids:
            tv_history.extend(_fetch_all_history(url, token, aid, sid))
        log.info(f"Plex: account '{fname}' has {len(tv_history)} TV plays")

        # Aggregate by grandparentTitle (show) and by season
        by_show: dict[str, dict] = {}
        by_season: dict[str, dict] = {}  # key: "show_title:season_num"
        for entry in tv_history:
            show_title = entry.get("grandparentTitle", "")
            season = entry.get("parentIndex")
            episode = entry.get("index")
            viewed_at = entry.get("viewedAt")
            ts = int(viewed_at) if viewed_at else None

            if show_title not in by_show:
                by_show[show_title] = {"plays": 0, "last_viewed_at": None, "unique_episodes": set()}
            rec = by_show[show_title]
            rec["plays"] += 1
            if ts and (rec["last_viewed_at"] is None or ts > rec["last_viewed_at"]):
                rec["last_viewed_at"] = ts
            if season and episode:
                rec["unique_episodes"].add((season, episode))

            # Per-season aggregation
            if season:
                skey = f"{show_title}:{season}"
                if skey not in by_season:
                    by_season[skey] = {
                        "show_title": show_title,
                        "season_num": int(season),
                        "plays": 0,
                        "last_viewed_at": None,
                        "unique_episodes": set(),
                    }
                srec = by_season[skey]
                srec["plays"] += 1
                if ts and (srec["last_viewed_at"] is None or ts > srec["last_viewed_at"]):
                    srec["last_viewed_at"] = ts
                if episode:
                    srec["unique_episodes"].add(episode)

        for show_title, rec in by_show.items():
            meta = show_meta.get(show_title)
            if not meta or not meta["tmdb_id"]:
                continue
            tmdb_id = meta["tmdb_id"]
            leaf_count = meta["leaf_count"] or 1
            unique_eps = len(rec["unique_episodes"])
            completion_pct = round(min(100, unique_eps / leaf_count * 100), 1)
            last_watched = (
                datetime.fromtimestamp(rec["last_viewed_at"]).strftime("%Y-%m-%d")
                if rec["last_viewed_at"] else None
            )
            if tmdb_id not in tv_watch:
                tv_watch[tmdb_id] = {"_plex_key": meta["rating_key"]}
            tv_watch[tmdb_id][fname] = {
                "plays": rec["plays"],
                "duration_seconds": 0,
                "last_watched": last_watched,
                "completion_pct": completion_pct,
                "unique_episodes_watched": unique_eps,
                "total_episodes": leaf_count,
            }

        # Season-level watch data
        for skey, srec in by_season.items():
            show_title = srec["show_title"]
            meta = show_meta.get(show_title)
            if not meta or not meta["tmdb_id"]:
                continue
            tmdb_id = meta["tmdb_id"]
            snum = srec["season_num"]
            unique_eps = len(srec["unique_episodes"])
            last_watched = (
                datetime.fromtimestamp(srec["last_viewed_at"]).strftime("%Y-%m-%d")
                if srec["last_viewed_at"] else None
            )
            if tmdb_id not in tv_season_watch:
                tv_season_watch[tmdb_id] = {}
            if snum not in tv_season_watch[tmdb_id]:
                tv_season_watch[tmdb_id][snum] = {}
            tv_season_watch[tmdb_id][snum][fname] = {
                "plays": srec["plays"],
                "unique_episodes_watched": unique_eps,
                "last_watched": last_watched,
            }

        # Movie history — aggregate across all movie section IDs
        movie_history = []
        for sid in movie_section_ids:
            movie_history.extend(_fetch_all_history(url, token, aid, sid))
        log.info(f"Plex: account '{fname}' has {len(movie_history)} movie plays")

        by_movie: dict[str, dict] = {}
        for entry in movie_history:
            title = entry.get("title", "")
            viewed_at = entry.get("viewedAt")
            if title not in by_movie:
                by_movie[title] = {"plays": 0, "last_viewed_at": None}
            rec = by_movie[title]
            rec["plays"] += 1
            if viewed_at:
                ts = int(viewed_at)
                if rec["last_viewed_at"] is None or ts > rec["last_viewed_at"]:
                    rec["last_viewed_at"] = ts

        for movie_title, rec in by_movie.items():
            meta = movie_meta.get(movie_title)
            if not meta or not meta["tmdb_id"]:
                continue
            tmdb_id = meta["tmdb_id"]
            last_watched = (
                datetime.fromtimestamp(rec["last_viewed_at"]).strftime("%Y-%m-%d")
                if rec["last_viewed_at"] else None
            )
            if tmdb_id not in movie_watch:
                movie_watch[tmdb_id] = {"_plex_key": meta["rating_key"]}
            movie_watch[tmdb_id][fname] = {
                "plays": rec["plays"],
                "duration_seconds": 0,
                "last_watched": last_watched,
                "completion_pct": 100,
            }

    log.info(f"Plex: aggregated watch data for {len(tv_watch)} shows, {len(movie_watch)} movies")

    user_list = [
        {"user_id": a["id"], "friendly_name": (name_map or {}).get(a["name"], a["name"])}
        for a in accounts
    ]
    return {
        "tv": tv_watch, "tv_seasons": tv_season_watch,
        "movie": movie_watch, "users": user_list,
        "machine_id": machine_id,
    }
