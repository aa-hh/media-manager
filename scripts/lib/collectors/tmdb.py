"""
Fetches enriched metadata and artwork from TMDB.
Results are cached to avoid redundant API calls across runs.
"""
import json
import requests
from pathlib import Path
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from .. import log

POSTER_SIZE = "w342"
BACKDROP_SIZE = "w1280"
BASE_IMG = "https://image.tmdb.org/t/p"


def _img(path: str | None, size: str) -> str | None:
    if not path:
        return None
    return f"{BASE_IMG}/{size}{path}"


def _fetch_one(tmdb_id: int, media_type: str, api_key: str) -> dict | None:
    url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
    try:
        resp = requests.get(url, params={"api_key": api_key}, timeout=30, verify=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        d = resp.json()
        genres = [g["name"] for g in d.get("genres", [])]
        return {
            "tmdb_id": tmdb_id,
            "poster": _img(d.get("poster_path"), POSTER_SIZE),
            "backdrop": _img(d.get("backdrop_path"), BACKDROP_SIZE),
            "rating": d.get("vote_average"),
            "vote_count": d.get("vote_count"),
            "genres": genres,
            "overview": d.get("overview", ""),
            "runtime": d.get("runtime") or (d.get("episode_run_time") or [None])[0],
            "status": d.get("status", ""),
        }
    except Exception as e:
        log.warn(f"TMDB fetch failed for {media_type}/{tmdb_id}: {e}")
        return None


def enrich(
    items: list[dict],
    media_type: str,
    api_key: str,
    cache_file: Path,
    max_workers: int = 8,
) -> dict[int, dict]:
    """
    Returns a dict: { tmdb_id: enriched_data }
    Reads/writes cache_file to avoid re-fetching.
    """
    try:
        cache = json.loads(cache_file.read_text())
    except Exception:
        cache = {}

    lock = Lock()
    needed = [
        item["tmdb_id"]
        for item in items
        if item.get("tmdb_id") and str(item["tmdb_id"]) not in cache
    ]
    needed = list(set(needed))

    if needed:
        log.info(f"TMDB: fetching {len(needed)} new {media_type} records")

        def fetch_and_cache(tmdb_id: int):
            result = _fetch_one(tmdb_id, media_type, api_key)
            if result:
                with lock:
                    cache[str(tmdb_id)] = result
            return tmdb_id, result

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(fetch_and_cache, needed))

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, indent=2))
        log.info(f"TMDB: cache updated ({len(cache)} total {media_type} entries)")

    return {int(k): v for k, v in cache.items()}
