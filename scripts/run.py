#!/usr/bin/env python3
"""
Media Manager — main entry point.

Usage:
  python run.py                 # full pipeline: collect + generate
  python run.py all             # same as above
  python run.py collect         # run all collectors then build
  python run.py generate        # regenerate HTML (requires data/*.json)
  python run.py build           # rebuild final data files from raw intermediates
  python run.py sonarr          # fetch Sonarr TV library only
  python run.py radarr          # fetch Radarr movie library only
  python run.py overseerr       # fetch Overseerr requests & watchlists only
  python run.py plex            # fetch Plex watch history only
  python run.py tautulli        # fetch Tautulli watch data only
  python run.py tmdb            # run TMDB metadata enrichment only
  python run.py services        # check service health & versions only
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
PUBLIC_DIR = ROOT / "public"
CACHE_DIR = ROOT / "cache"
LOG_DIR = ROOT / "logs"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
CONFIG_DIR = ROOT / "config"

sys.path.insert(0, str(Path(__file__).parent))

from lib import config, log
from lib.collectors import sonarr, radarr, overseerr, tautulli, tmdb, plex, services
from lib.processors import enrichment, deletion, forecasting


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_user_identities() -> tuple[dict, dict]:
    users_file = CONFIG_DIR / "users.json"
    if not users_file.exists():
        return {}, {}
    users = json.loads(users_file.read_text())
    plex_map: dict = {}
    seerr_map: dict = {}
    for u in users:
        name = u["name"]
        for plex_name in u.get("plex_names", []):
            plex_map[plex_name] = name
        if u.get("seerr_id"):
            seerr_map[u["seerr_id"]] = name
    return plex_map, seerr_map


def _record_run(job_id: str) -> None:
    """Write last-run timestamp for job_id to data/job_runs.json."""
    runs_file = DATA_DIR / "job_runs.json"
    try:
        runs = json.loads(runs_file.read_text()) if runs_file.exists() else {}
    except Exception:
        runs = {}
    runs[job_id] = datetime.now(timezone.utc).isoformat()
    runs_file.write_text(json.dumps(runs, indent=2))


def _load_raw(name: str, default):
    path = DATA_DIR / f"raw_{name}.json"
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _remap_names(watch_data: dict, name_map: dict) -> dict:
    """Apply a name_map to the user keys inside a watch data dict (tv/movie/tv_seasons/users)."""
    def _remap_store(store: dict) -> dict:
        out: dict = {}
        for tmdb_id, by_user in store.items():
            remapped: dict = {}
            for user, stats in by_user.items():
                remapped[name_map.get(user, user)] = stats
            out[tmdb_id] = remapped
        return out

    def _remap_seasons(seasons: dict) -> dict:
        out: dict = {}
        for tmdb_id, by_season in seasons.items():
            out[tmdb_id] = {}
            for snum, by_user in by_season.items():
                out[tmdb_id][snum] = {name_map.get(u, u): s for u, s in by_user.items()}
        return out

    return {
        "tv":         _remap_store(watch_data.get("tv", {})),
        "movie":      _remap_store(watch_data.get("movie", {})),
        "tv_seasons": _remap_seasons(watch_data.get("tv_seasons", {})),
        "users": [
            {**u, "friendly_name": name_map.get(u["friendly_name"], u["friendly_name"])}
            for u in watch_data.get("users", [])
        ],
    }


def _cfg() -> dict:
    return {
        "sonarr_urls": [u.strip() for u in config.get("SONARR_URL", "").split(",") if u.strip()],
        "sonarr_keys": [k.strip() for k in config.get("SONARR_API_KEY", "").split(",") if k.strip()],
        "radarr_urls": [u.strip() for u in config.get("RADARR_URL", "").split(",") if u.strip()],
        "radarr_keys": [k.strip() for k in config.get("RADARR_API_KEY", "").split(",") if k.strip()],
        "seerr_url": config.get("SEERR_URL", ""),
        "seerr_key": config.get("SEERR_API_KEY", ""),
        "tautulli_url": config.get("TAUTULLI_URL", ""),
        "tautulli_key": config.get("TAUTULLI_API_KEY", ""),
        "plex_url": config.get("PLEX_URL", "").rstrip("/"),
        "plex_token": config.get("PLEX_TOKEN", ""),
        "tmdb_key": config.get("TMDB_API_KEY", ""),
        "tv_section_ids": [s.strip() for s in config.get("PLEX_TV_SECTIONS", "1").split(",") if s.strip()],
        "movie_section_ids": [s.strip() for s in config.get("PLEX_MOVIE_SECTIONS", "2").split(",") if s.strip()],
    }


# ── Individual collectors ─────────────────────────────────────────────────────

def fetch_sonarr(_build: bool = True) -> None:
    log.info("=== Sonarr fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    items: list = []
    if not c["sonarr_urls"]:
        log.info("Sonarr not configured — skipping")
    else:
        for i, url in enumerate(c["sonarr_urls"]):
            key = c["sonarr_keys"][i] if i < len(c["sonarr_keys"]) else (c["sonarr_keys"][0] if c["sonarr_keys"] else "")
            try:
                items.extend(sonarr.fetch(url, key))
            except Exception as e:
                log.warn(f"Sonarr instance {url} unavailable: {e}")
    (DATA_DIR / "raw_sonarr.json").write_text(json.dumps(items, indent=2))
    log.info(f"Sonarr: {len(items)} items saved")
    _record_run("sonarr")
    log.info("=== Sonarr fetch complete ===")
    if _build:
        build()


def fetch_radarr(_build: bool = True) -> None:
    log.info("=== Radarr fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    items: list = []
    if not c["radarr_urls"]:
        log.info("Radarr not configured — skipping")
    else:
        for i, url in enumerate(c["radarr_urls"]):
            key = c["radarr_keys"][i] if i < len(c["radarr_keys"]) else (c["radarr_keys"][0] if c["radarr_keys"] else "")
            try:
                items.extend(radarr.fetch(url, key))
            except Exception as e:
                log.warn(f"Radarr instance {url} unavailable: {e}")
    (DATA_DIR / "raw_radarr.json").write_text(json.dumps(items, indent=2))
    log.info(f"Radarr: {len(items)} items saved")
    _record_run("radarr")
    log.info("=== Radarr fetch complete ===")
    if _build:
        build()


def fetch_overseerr(_build: bool = True) -> None:
    log.info("=== Overseerr fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    _, seerr_id_to_canonical = _load_user_identities()
    requests_list: list = []
    users: dict = {}
    watchlist: dict = {}
    if c["seerr_url"] and c["seerr_key"]:
        try:
            requests_list, users = overseerr.fetch(c["seerr_url"], c["seerr_key"])
        except Exception as e:
            log.warn(f"Overseerr unavailable: {e}")
        try:
            watchlist = overseerr.fetch_watchlist(c["seerr_url"], c["seerr_key"], users)
        except Exception as e:
            log.warn(f"Overseerr watchlist unavailable: {e}")
        if seerr_id_to_canonical:
            for r in requests_list:
                rid = r.get("requester_id")
                if rid and rid in seerr_id_to_canonical:
                    r["requester_name"] = seerr_id_to_canonical[rid]
    else:
        log.info("Overseerr not configured — skipping")
    (DATA_DIR / "raw_overseerr.json").write_text(json.dumps({
        "requests": requests_list,
        "users": users,
        "watchlist": [[mt, tid, sorted(uids)] for (mt, tid), uids in sorted(watchlist.items())],
    }, indent=2))
    log.info(f"Overseerr: {len(requests_list)} requests saved")
    _record_run("overseerr")
    log.info("=== Overseerr fetch complete ===")
    if _build:
        build()


def fetch_watch_history(_build: bool = True) -> None:
    """Fetch watch history from Plex and/or Tautulli — whichever are configured."""
    log.info("=== Watch history fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    plex_to_canonical, _ = _load_user_identities()

    plex_data: dict = {"tv": {}, "movie": {}, "users": [], "machine_id": "", "tv_seasons": {}}
    if c["plex_url"] and c["plex_token"]:
        try:
            plex_data = plex.fetch(c["plex_url"], c["plex_token"], name_map=plex_to_canonical,
                                   tv_section_ids=c["tv_section_ids"], movie_section_ids=c["movie_section_ids"])
            log.info("Plex watch data fetched")
        except Exception as e:
            log.warn(f"Plex unavailable: {e}")
    else:
        log.info("Plex not configured — skipping")
    (DATA_DIR / "raw_plex.json").write_text(json.dumps(plex_data, indent=2))

    tautulli_data: dict = {"tv": {}, "movie": {}, "users": [], "tv_seasons": {}}
    if c["tautulli_url"] and c["tautulli_key"]:
        try:
            tautulli_data = tautulli.fetch(c["tautulli_url"], c["tautulli_key"],
                                           tv_section_ids=c["tv_section_ids"], movie_section_ids=c["movie_section_ids"])
            if plex_to_canonical:
                tautulli_data = _remap_names(tautulli_data, plex_to_canonical)
            log.info("Tautulli watch data fetched")
        except Exception as e:
            log.warn(f"Tautulli unavailable: {e}")
    else:
        log.info("Tautulli not configured — skipping")
    (DATA_DIR / "raw_tautulli.json").write_text(json.dumps(tautulli_data, indent=2))

    _record_run("watch")
    log.info("=== Watch history fetch complete ===")
    if _build:
        build()


def fetch_tmdb(_build: bool = True) -> None:
    log.info("=== TMDB enrichment started ===")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    if not c["tmdb_key"]:
        log.info("TMDB_API_KEY not set — skipping")
        _record_run("tmdb")
        return
    sonarr_items = _load_raw("sonarr", [])
    radarr_items = _load_raw("radarr", [])
    tmdb.enrich(sonarr_items, "tv", c["tmdb_key"], CACHE_DIR / "tmdb_tv.json")
    tmdb.enrich(radarr_items, "movie", c["tmdb_key"], CACHE_DIR / "tmdb_movies.json")
    _record_run("tmdb")
    log.info("=== TMDB enrichment complete ===")
    if _build:
        build()


def fetch_services() -> None:
    log.info("=== Services check started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    services_data = services.collect()
    (DATA_DIR / "services.json").write_text(json.dumps(services_data, indent=2))
    _record_run("services")
    log.info("=== Services check complete ===")


# ── Webhook helpers ───────────────────────────────────────────────────────────

def _load_webhook_transcode() -> tuple[dict, dict]:
    """Aggregate webhook_plays.db into (transcode_tv, transcode_movie) dicts."""
    import sqlite3
    db_path = DATA_DIR / "webhook_plays.db"
    if not db_path.exists():
        return {}, {}

    tv: dict[int, dict] = {}
    movie: dict[int, dict] = {}

    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("""
            SELECT tmdb_id, media_type, transcode_decision, quality_profile
            FROM plays
            WHERE tmdb_id IS NOT NULL AND event = 'play'
        """).fetchall()
        con.close()
    except Exception as e:
        log.warn(f"Could not read webhook_plays.db: {e}")
        return {}, {}

    for tmdb_id, media_type, decision, qp in rows:
        mt = (media_type or "").lower()
        store = tv if mt in ("episode", "show", "tv") else movie
        decision = (decision or "").lower()
        qp = qp or ""

        existing = store.get(tmdb_id, {})
        merged_q = dict(existing.get("transcode_qualities", {}))
        if decision == "transcode" and qp:
            merged_q[qp] = merged_q.get(qp, 0) + 1

        store[tmdb_id] = {
            "direct":              existing.get("direct", 0)    + (1 if decision == "direct play" else 0),
            "transcode":           existing.get("transcode", 0) + (1 if decision == "transcode" else 0),
            "copy":                existing.get("copy", 0)      + (1 if decision == "copy" else 0),
            "total":               existing.get("total", 0)     + 1,
            "transcode_qualities": merged_q,
            "avg_watch_pct":       None,
        }

    return tv, movie


def _merge_transcode(base: dict, overlay: dict) -> dict:
    """Merge overlay counts into base, combining counts for shared tmdb_ids."""
    merged = dict(base)
    for tid, stats in overlay.items():
        if tid not in merged:
            merged[tid] = stats
        else:
            existing = merged[tid]
            merged_q = dict(existing.get("transcode_qualities", {}))
            for qp, cnt in stats.get("transcode_qualities", {}).items():
                merged_q[qp] = merged_q.get(qp, 0) + cnt
            merged[tid] = {
                "direct":              existing.get("direct", 0)    + stats.get("direct", 0),
                "transcode":           existing.get("transcode", 0) + stats.get("transcode", 0),
                "copy":                existing.get("copy", 0)      + stats.get("copy", 0),
                "total":               existing.get("total", 0)     + stats.get("total", 0),
                "transcode_qualities": merged_q,
                "avg_watch_pct":       existing.get("avg_watch_pct"),
            }
    return merged


# ── Build ─────────────────────────────────────────────────────────────────────

def build() -> None:
    """Combine all raw intermediate files into the final data files."""
    log.info("=== Build started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sonarr_items = _load_raw("sonarr", [])
    radarr_items = _load_raw("radarr", [])
    ov_data = _load_raw("overseerr", {"requests": [], "users": {}, "watchlist": []})
    plex_data = _load_raw("plex", {"tv": {}, "movie": {}, "users": [], "machine_id": "", "tv_seasons": {}})
    tautulli_data = _load_raw("tautulli", {"tv": {}, "movie": {}, "users": [], "tv_seasons": {}})

    ov_requests: list = ov_data.get("requests", [])
    ov_users: dict = ov_data.get("users", {})
    # Each entry is [media_type, tmdb_id, [user_ids]]; tolerate the older 2-element
    # format (no per-user attribution) from caches written before this change.
    watchlist_raw = ov_data.get("watchlist", [])
    watchlist = [(entry[0], entry[1], set(entry[2]) if len(entry) > 2 else set()) for entry in watchlist_raw]

    # Load TMDB from cache (fetch_tmdb writes these; skip API calls here)
    def _load_tmdb_cache(path: Path) -> dict:
        try:
            raw = json.loads(path.read_text())
            return {int(k): v for k, v in raw.items()}
        except Exception:
            return {}

    tv_tmdb = _load_tmdb_cache(CACHE_DIR / "tmdb_tv.json")
    movie_tmdb = _load_tmdb_cache(CACHE_DIR / "tmdb_movies.json")

    watch_data = _merge_watch_data(plex_data, tautulli_data)

    # Transcode stats come from Tautulli only (Plex doesn't expose them)
    def _int_key_dict(d: dict) -> dict:
        try:
            return {int(k): v for k, v in d.items()}
        except Exception:
            return d

    raw_transcode = tautulli_data.get("transcode_stats", {})
    transcode_tv    = _int_key_dict(raw_transcode.get("tv", {}))
    transcode_movie = _int_key_dict(raw_transcode.get("movie", {}))

    # Supplement with webhook play events from the DB
    wh_tv, wh_movie = _load_webhook_transcode()
    if wh_tv or wh_movie:
        transcode_tv    = _merge_transcode(transcode_tv, wh_tv)
        transcode_movie = _merge_transcode(transcode_movie, wh_movie)
        log.info(f"Webhook DB: merged {len(wh_tv)} TV + {len(wh_movie)} movie transcode entries")

    (DATA_DIR / "watchlist.json").write_text(json.dumps(
        [[mt, tid, sorted(uids)] for mt, tid, uids in watchlist], indent=2
    ))

    # tmdb_id -> set of overseerr user_ids who have it watchlisted, per media type
    watchlist_tv: dict[int, set[int]] = {}
    watchlist_movies: dict[int, set[int]] = {}
    for mt, tid, uids in watchlist:
        target = watchlist_tv if mt == "tv" else watchlist_movies
        target.setdefault(tid, set()).update(uids)

    shows = enrichment.build_shows(
        sonarr_items, tv_tmdb, ov_requests, watch_data["tv"],
        watch_data.get("tv_seasons", {}),
        transcode_stats=transcode_tv,
        watchlist=watchlist_tv,
    )
    movies = enrichment.build_movies(
        radarr_items, movie_tmdb, ov_requests, watch_data["movie"],
        transcode_stats=transcode_movie,
        watchlist=watchlist_movies,
    )

    deletion.apply(shows)
    deletion.apply(movies)
    forecasting.record_snapshot(DATA_DIR, shows, movies)

    users = _build_users(shows, movies, ov_users, watch_data.get("users", []))
    machine_id = plex_data.get("machine_id", "")

    (DATA_DIR / "tv.json").write_text(json.dumps(shows, indent=2))
    (DATA_DIR / "movies.json").write_text(json.dumps(movies, indent=2))
    (DATA_DIR / "users.json").write_text(json.dumps(users, indent=2))
    (DATA_DIR / "requests.json").write_text(json.dumps(ov_requests, indent=2))
    (DATA_DIR / "plex_meta.json").write_text(json.dumps({"machine_id": machine_id}))

    log.info(f"Build: {len(shows)} shows, {len(movies)} movies, {len(users)} users")
    _record_run("build")
    log.info("=== Build complete ===")


# ── Aggregate operations ──────────────────────────────────────────────────────

def collect() -> None:
    log.info("=== Collect phase started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    c = _cfg()
    if not c["sonarr_urls"] and not c["radarr_urls"]:
        raise RuntimeError("At least one of SONARR_URL or RADARR_URL must be configured.")

    fetch_sonarr(_build=False)
    fetch_radarr(_build=False)
    fetch_tmdb(_build=False)
    fetch_overseerr(_build=False)
    fetch_watch_history(_build=False)
    fetch_services()
    build()

    _record_run("collect")
    log.info("=== Collect phase complete ===")


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
    _record_run("generate")
    log.info("=== Generate phase complete ===")


# ── Merge helpers (unchanged from original) ───────────────────────────────────

def _merge_watch_data(plex_data: dict, tautulli_data: dict) -> dict:
    def _int_keys(store: dict, depth: int = 1) -> dict:
        """Normalise string integer keys to int (JSON round-trip converts int keys to str).

        depth=1 converts only the top-level keys; depth=2 also converts the next level
        (used for tv_seasons whose structure is tmdb_id → season_number → ...).
        """
        out = {}
        for k, v in store.items():
            try:
                ik = int(k)
            except (ValueError, TypeError):
                ik = k
            if depth > 1 and isinstance(v, dict):
                v = _int_keys(v, depth - 1)
            out[ik] = v
        return out

    def _merge_entry(p: dict, t: dict) -> dict:
        """Pick the more complete record by play count, but always keep the
        most recent last_watched across both sources — a source "winning" on
        play count shouldn't discard an older-but-real date the other source has.
        """
        winner = t if (t.get("plays", 0) >= p.get("plays", 0)) else p
        dates = [d for d in (p.get("last_watched"), t.get("last_watched")) if d]
        merged_entry = dict(winner)
        if dates:
            merged_entry["last_watched"] = max(dates)
        return merged_entry

    def _merge_by_item(plex_store, taut_store):
        plex_store = _int_keys(plex_store)
        taut_store = _int_keys(taut_store)
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
                merged[tmdb_id][user] = _merge_entry(p, t)
        return merged

    merged_tv = _merge_by_item(plex_data.get("tv", {}), tautulli_data.get("tv", {}))
    merged_movie = _merge_by_item(plex_data.get("movie", {}), tautulli_data.get("movie", {}))

    seen: set = set()
    users = []
    for u in plex_data.get("users", []) + tautulli_data.get("users", []):
        if u["friendly_name"] not in seen:
            seen.add(u["friendly_name"])
            users.append(u)

    tv_count = sum(len(v) for v in merged_tv.values())
    movie_count = sum(len(v) for v in merged_movie.values())
    log.info(f"Merged watch data: {len(merged_tv)} shows ({tv_count} user entries), "
             f"{len(merged_movie)} movies ({movie_count} user entries)")

    merged_seasons: dict = {}
    plex_seasons = _int_keys(plex_data.get("tv_seasons", {}), depth=2)
    taut_seasons = _int_keys(tautulli_data.get("tv_seasons", {}), depth=2)
    for tmdb_id in set(plex_seasons) | set(taut_seasons):
        merged_seasons[tmdb_id] = {}
        p_s = plex_seasons.get(tmdb_id, {})
        t_s = taut_seasons.get(tmdb_id, {})
        for snum in set(p_s) | set(t_s):
            p_users = p_s.get(snum, {})
            t_users = t_s.get(snum, {})
            merged_seasons[tmdb_id][snum] = {}
            for user in set(p_users) | set(t_users):
                p = p_users.get(user, {})
                t = t_users.get(user, {})
                merged_seasons[tmdb_id][snum][user] = _merge_entry(p, t)

    return {"tv": merged_tv, "tv_seasons": merged_seasons, "movie": merged_movie, "users": users}


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
        requests_made = [item["id"] for item in shows + movies
                         if item.get("request", {}).get("requester_name") == name]
        watched_items = [item["id"] for item in shows + movies
                         if item.get("watch_data", {}).get(name, {}).get("plays", 0) > 0]
        storage_requested = sum(item["size_gb"] for item in (shows + movies)
                                if item.get("request", {}).get("requester_name") == name)
        storage_watched = sum(item["size_gb"] for item in (shows + movies)
                              if item.get("watch_data", {}).get(name, {}).get("plays", 0) > 0)
        total_plays = sum(item.get("watch_data", {}).get(name, {}).get("plays", 0)
                          for item in (shows + movies))
        unwatched_requests = [item["id"] for item in (shows + movies)
                              if item.get("request", {}).get("requester_name") == name
                              and not item.get("requester_status", {}).get("watched", False)]
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


def _get_capacity() -> float | None:
    val = config.get("STORAGE_CAPACITY_GB", "")
    try:
        return float(val) if val else None
    except ValueError:
        return None


# ── Entry point ───────────────────────────────────────────────────────────────

COMMANDS: dict = {
    "sonarr":    fetch_sonarr,
    "radarr":    fetch_radarr,
    "overseerr": fetch_overseerr,
    "watch":     fetch_watch_history,
    "tmdb":      fetch_tmdb,
    "services":  fetch_services,
    "build":     build,
    "collect":   collect,
    "generate":  generate,
}


def main() -> None:
    start = datetime.now()
    log.init(LOG_DIR)
    config.load()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd == "all":
        collect()
        generate()
        _record_run("all")
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"Unknown command: {cmd!r}. Valid: {', '.join(sorted(COMMANDS))} all", file=sys.stderr)
        sys.exit(1)

    duration = (datetime.now() - start).total_seconds()
    log.info(f"Done in {duration:.1f}s")


if __name__ == "__main__":
    main()
