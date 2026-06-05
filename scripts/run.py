#!/usr/bin/env python3
"""
Media Manager — main entry point.

Usage:
  python run.py           # collect + generate
  python run.py collect   # collect data only → data/*.json
  python run.py generate  # generate HTML only (requires data/*.json)
"""
import sys
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
CACHE_DIR = ROOT / "cache"
LOG_DIR = ROOT / "logs"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
CONFIG_DIR = ROOT / "config"


def _load_user_identities() -> tuple[dict, dict]:
    """
    Returns (plex_to_canonical, seerr_id_to_canonical).
    Both map to the canonical user name defined in config/users.json.
    """
    users_file = CONFIG_DIR / "users.json"
    if not users_file.exists():
        return {}, {}
    users = json.loads(users_file.read_text())
    plex_map = {}
    seerr_map = {}
    for u in users:
        name = u["name"]
        for plex_name in u.get("plex_names", []):
            plex_map[plex_name] = name
        if u.get("seerr_id"):
            seerr_map[u["seerr_id"]] = name
    return plex_map, seerr_map

sys.path.insert(0, str(Path(__file__).parent))

from lib import config, log
from lib.collectors import sonarr, radarr, overseerr, tautulli, tmdb, plex, services
from lib.processors import enrichment, deletion, forecasting


def collect() -> None:
    log.info("=== Collect phase started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    plex_to_canonical, seerr_id_to_canonical = _load_user_identities()
    log.info(f"User identity map: {plex_to_canonical}")

    sonarr_url = config.require("SONARR_URL")
    sonarr_key = config.require("HOMEPAGE_VAR_SONARR_API_KEY")
    radarr_url = config.require("RADARR_URL")
    radarr_key = config.require("HOMEPAGE_VAR_RADARR_API_KEY")
    seerr_url = config.require("SEERR_URL")
    seerr_key = config.require("HOMEPAGE_VAR_SEERR_API_KEY")
    tautulli_url = config.get("TAUTULLI_URL", "")
    tautulli_key = config.get("TAUTULLI_API_KEY", "")
    plex_url = config.get("PLEX_URL", "")
    plex_token = config.get("HOMEPAGE_VAR_PLEX_TOKEN", "")
    tmdb_key = config.require("TMDB_API_KEY")

    # Fetch from Sonarr and Radarr
    sonarr_items = sonarr.fetch(sonarr_url, sonarr_key)
    radarr_items = radarr.fetch(radarr_url, radarr_key)

    # Fetch TMDB enrichment
    tv_tmdb = tmdb.enrich(sonarr_items, "tv", tmdb_key, CACHE_DIR / "tmdb_tv.json")
    movie_tmdb = tmdb.enrich(radarr_items, "movie", tmdb_key, CACHE_DIR / "tmdb_movies.json")

    # Fetch Overseerr requests + watchlist
    try:
        ov_requests, ov_users = overseerr.fetch(seerr_url, seerr_key)
    except Exception as e:
        log.warn(f"Overseerr unavailable: {e}")
        ov_requests, ov_users = [], {}

    watchlist: set[tuple] = set()
    try:
        watchlist = overseerr.fetch_watchlist(seerr_url, seerr_key, ov_users)
    except Exception as e:
        log.warn(f"Overseerr watchlist unavailable: {e}")
    (DATA_DIR / "watchlist.json").write_text(
        json.dumps([[mt, tid] for mt, tid in sorted(watchlist)], indent=2)
    )

    # Fetch watch data from both sources and merge.
    # Plex provides full historical backfill; Tautulli provides richer real-time data.
    # For each user+item, the source with more plays wins.
    plex_data = {"tv": {}, "movie": {}, "users": []}
    tautulli_watch = {"tv": {}, "movie": {}, "users": []}

    if plex_url and plex_token:
        try:
            plex_data = plex.fetch(plex_url, plex_token, name_map=plex_to_canonical)
            log.info("Plex watch data fetched")
        except Exception as e:
            log.warn(f"Plex watch history unavailable: {e}")

    if tautulli_url and tautulli_key:
        try:
            tautulli_watch = tautulli.fetch(tautulli_url, tautulli_key)
            log.info("Tautulli watch data fetched")
        except Exception as e:
            log.warn(f"Tautulli unavailable: {e}")

    watch_data = _merge_watch_data(plex_data, tautulli_watch)

    # Normalise Overseerr requester names to canonical
    if seerr_id_to_canonical:
        for r in ov_requests:
            rid = r.get("requester_id")
            if rid and rid in seerr_id_to_canonical:
                r["requester_name"] = seerr_id_to_canonical[rid]

    tautulli_data = watch_data  # alias used below

    # Build canonical records
    shows = enrichment.build_shows(
        sonarr_items, tv_tmdb, ov_requests, tautulli_data["tv"],
        tautulli_data.get("tv_seasons", {}),
    )
    movies = enrichment.build_movies(
        radarr_items, movie_tmdb, ov_requests, tautulli_data["movie"]
    )

    # Apply deletion scoring
    deletion.apply(shows)
    deletion.apply(movies)

    # Record storage snapshot
    forecasting.record_snapshot(DATA_DIR, shows, movies)

    # Build user profiles from Overseerr users + Tautulli users
    users = _build_users(shows, movies, ov_users, tautulli_data.get("users", []))

    # Persist
    (DATA_DIR / "tv.json").write_text(json.dumps(shows, indent=2))
    (DATA_DIR / "movies.json").write_text(json.dumps(movies, indent=2))
    (DATA_DIR / "users.json").write_text(json.dumps(users, indent=2))
    (DATA_DIR / "requests.json").write_text(json.dumps(ov_requests, indent=2))
    machine_id = plex_data.get("machine_id", "") if plex_data else ""
    (DATA_DIR / "plex_meta.json").write_text(json.dumps({"machine_id": machine_id}))

    log.info(f"Saved {len(shows)} shows, {len(movies)} movies, {len(users)} users to data/")

    # Services health + version check
    services_data = services.collect()
    (DATA_DIR / "services.json").write_text(json.dumps(services_data, indent=2))

    log.info("=== Collect phase complete ===")


def _merge_watch_data(plex_data: dict, tautulli_data: dict) -> dict:
    """
    Merges Plex and Tautulli watch data.
    For each (tmdb_id, user), the source with more plays wins.
    Tautulli wins ties since it has richer duration/transcode data.
    Season-level data comes from Plex only (Tautulli doesn't provide it yet).
    """
    def _merge_by_item(plex_store, taut_store):
        merged = {}
        for tmdb_id in set(plex_store) | set(taut_store):
            plex_item = plex_store.get(tmdb_id, {})
            taut_item = taut_store.get(tmdb_id, {})
            plex_users = {k: v for k, v in plex_item.items() if not k.startswith("_")}
            taut_users = {k: v for k, v in taut_item.items() if not k.startswith("_")}
            merged[tmdb_id] = {"_plex_key": plex_item.get("_plex_key")}
            for user in set(plex_users) | set(taut_users):
                p = plex_users.get(user, {})
                t = taut_users.get(user, {})
                merged[tmdb_id][user] = t if (t.get("plays", 0) >= p.get("plays", 0)) else p
        return merged

    merged_tv = _merge_by_item(plex_data.get("tv", {}), tautulli_data.get("tv", {}))
    merged_movie = _merge_by_item(plex_data.get("movie", {}), tautulli_data.get("movie", {}))

    seen = set()
    users = []
    for u in plex_data.get("users", []) + tautulli_data.get("users", []):
        if u["friendly_name"] not in seen:
            seen.add(u["friendly_name"])
            users.append(u)

    tv_count = sum(len(v) for v in merged_tv.values())
    movie_count = sum(len(v) for v in merged_movie.values())
    log.info(f"Merged watch data: {len(merged_tv)} shows ({tv_count} user entries), {len(merged_movie)} movies ({movie_count} user entries)")

    # Merge season data — same play-count-wins logic per (tmdb_id, season, user)
    merged_seasons: dict = {}
    plex_seasons = plex_data.get("tv_seasons", {})
    taut_seasons = tautulli_data.get("tv_seasons", {})
    for tmdb_id in set(plex_seasons) | set(taut_seasons):
        merged_seasons[tmdb_id] = {}
        all_seasons = set(plex_seasons.get(tmdb_id, {})) | set(taut_seasons.get(tmdb_id, {}))
        for snum in all_seasons:
            p_users = plex_seasons.get(tmdb_id, {}).get(snum, {})
            t_users = taut_seasons.get(tmdb_id, {}).get(snum, {})
            merged_seasons[tmdb_id][snum] = {}
            for user in set(p_users) | set(t_users):
                p = p_users.get(user, {})
                t = t_users.get(user, {})
                merged_seasons[tmdb_id][snum][user] = t if (t.get("plays", 0) >= p.get("plays", 0)) else p

    return {
        "tv": merged_tv,
        "tv_seasons": merged_seasons,
        "movie": merged_movie,
        "users": users,
    }


def _build_users(shows: list[dict], movies: list[dict], ov_users: dict, tautulli_users: list) -> list[dict]:
    all_names: set[str] = set()

    for item in shows + movies:
        req = item.get("request", {})
        if req.get("requester_name"):
            all_names.add(req["requester_name"])
        for name in item.get("watch_data", {}).keys():
            all_names.add(name)

    users_out = []
    for name in sorted(all_names):
        requests_made = []
        for item in shows + movies:
            req = item.get("request", {})
            if req.get("requester_name") == name:
                requests_made.append(item["id"])

        watched_items = []
        for item in shows + movies:
            wd = item.get("watch_data", {}).get(name)
            if wd and wd.get("plays", 0) > 0:
                watched_items.append(item["id"])

        storage_requested = sum(
            item["size_gb"] for item in (shows + movies)
            if item.get("request", {}).get("requester_name") == name
        )
        storage_watched = sum(
            item["size_gb"] for item in (shows + movies)
            if item.get("watch_data", {}).get(name, {}).get("plays", 0) > 0
        )
        total_plays = sum(
            item.get("watch_data", {}).get(name, {}).get("plays", 0)
            for item in (shows + movies)
        )
        unwatched_requests = [
            item["id"] for item in (shows + movies)
            if item.get("request", {}).get("requester_name") == name
            and not item.get("requester_status", {}).get("watched", False)
        ]

        users_out.append({
            "name": name,
            "requests_made": len(requests_made),
            "requested_item_ids": requests_made,
            "storage_requested_gb": round(storage_requested, 2),
            "storage_watched_gb": round(storage_watched, 2),
            "total_plays": total_plays,
            "watched_item_ids": watched_items,
            "unwatched_request_ids": unwatched_requests,
        })

    return users_out


def generate() -> None:
    log.info("=== Generate phase started ===")

    try:
        shows = json.loads((DATA_DIR / "tv.json").read_text())
        movies = json.loads((DATA_DIR / "movies.json").read_text())
        users = json.loads((DATA_DIR / "users.json").read_text())
    except FileNotFoundError as e:
        raise RuntimeError(f"Data files missing — run 'collect' first: {e}")

    forecast = forecasting.calculate(DATA_DIR, capacity_gb=_get_capacity())

    services_data = {}
    services_file = DATA_DIR / "services.json"
    if services_file.exists():
        try:
            services_data = json.loads(services_file.read_text())
        except Exception:
            pass

    from generate import render_all
    render_all(
        shows=shows,
        movies=movies,
        users=users,
        forecast=forecast,
        services=services_data,
        public_dir=PUBLIC_DIR,
        templates_dir=TEMPLATES_DIR,
        assets_dir=ASSETS_DIR,
    )
    log.info("=== Generate phase complete ===")


def _get_capacity() -> float | None:
    val = config.get("STORAGE_CAPACITY_GB", "")
    try:
        return float(val) if val else None
    except ValueError:
        return None


def main() -> None:
    start = datetime.now()
    log.init(LOG_DIR)
    config.load()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd in ("collect", "all"):
        collect()
    if cmd in ("generate", "all"):
        generate()

    duration = (datetime.now() - start).total_seconds()
    log.info(f"Done in {duration:.1f}s")


if __name__ == "__main__":
    main()
