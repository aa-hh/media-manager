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

def fetch_sonarr() -> None:
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


def fetch_radarr() -> None:
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


def fetch_overseerr() -> None:
    log.info("=== Overseerr fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    _, seerr_id_to_canonical = _load_user_identities()
    requests_list: list = []
    users: dict = {}
    watchlist: set = set()
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
        "watchlist": [[mt, tid] for mt, tid in sorted(watchlist)],
    }, indent=2))
    log.info(f"Overseerr: {len(requests_list)} requests saved")
    _record_run("overseerr")
    log.info("=== Overseerr fetch complete ===")


def fetch_plex() -> None:
    log.info("=== Plex fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    plex_to_canonical, _ = _load_user_identities()
    data: dict = {"tv": {}, "movie": {}, "users": [], "machine_id": "", "tv_seasons": {}}
    if c["plex_url"] and c["plex_token"]:
        try:
            data = plex.fetch(c["plex_url"], c["plex_token"], name_map=plex_to_canonical,
                              tv_section_ids=c["tv_section_ids"], movie_section_ids=c["movie_section_ids"])
            log.info("Plex watch data fetched")
        except Exception as e:
            log.warn(f"Plex unavailable: {e}")
    else:
        log.info("Plex not configured — skipping")
    (DATA_DIR / "raw_plex.json").write_text(json.dumps(data, indent=2))
    _record_run("plex")
    log.info("=== Plex fetch complete ===")


def fetch_tautulli() -> None:
    log.info("=== Tautulli fetch started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = _cfg()
    data: dict = {"tv": {}, "movie": {}, "users": [], "tv_seasons": {}}
    if c["tautulli_url"] and c["tautulli_key"]:
        try:
            data = tautulli.fetch(c["tautulli_url"], c["tautulli_key"],
                                  tv_section_ids=c["tv_section_ids"], movie_section_ids=c["movie_section_ids"])
            log.info("Tautulli data fetched")
        except Exception as e:
            log.warn(f"Tautulli unavailable: {e}")
    else:
        log.info("Tautulli not configured — skipping")
    (DATA_DIR / "raw_tautulli.json").write_text(json.dumps(data, indent=2))
    _record_run("tautulli")
    log.info("=== Tautulli fetch complete ===")


def fetch_tmdb() -> None:
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


def fetch_services() -> None:
    log.info("=== Services check started ===")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    services_data = services.collect()
    (DATA_DIR / "services.json").write_text(json.dumps(services_data, indent=2))
    _record_run("services")
    log.info("=== Services check complete ===")


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
    watchlist = ov_data.get("watchlist", [])

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

    (DATA_DIR / "watchlist.json").write_text(json.dumps(watchlist, indent=2))

    shows = enrichment.build_shows(
        sonarr_items, tv_tmdb, ov_requests, watch_data["tv"],
        watch_data.get("tv_seasons", {}),
    )
    movies = enrichment.build_movies(
        radarr_items, movie_tmdb, ov_requests, watch_data["movie"]
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

    fetch_sonarr()
    fetch_radarr()
    fetch_tmdb()
    fetch_overseerr()
    fetch_plex()
    fetch_tautulli()
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
    plex_seasons = plex_data.get("tv_seasons", {})
    taut_seasons = tautulli_data.get("tv_seasons", {})
    for tmdb_id in set(plex_seasons) | set(taut_seasons):
        merged_seasons[tmdb_id] = {}
        for snum in set(plex_seasons.get(tmdb_id, {})) | set(taut_seasons.get(tmdb_id, {})):
            p_users = plex_seasons.get(tmdb_id, {}).get(snum, {})
            t_users = taut_seasons.get(tmdb_id, {}).get(snum, {})
            merged_seasons[tmdb_id][snum] = {}
            for user in set(p_users) | set(t_users):
                p = p_users.get(user, {})
                t = t_users.get(user, {})
                merged_seasons[tmdb_id][snum][user] = t if (t.get("plays", 0) >= p.get("plays", 0)) else p

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
    "plex":      fetch_plex,
    "tautulli":  fetch_tautulli,
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
