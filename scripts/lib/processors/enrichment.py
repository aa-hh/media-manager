"""
Builds canonical MediaItem records by joining data from all collectors.
"""
from slugify import slugify
from datetime import datetime, timezone


def _gb(size_bytes: int) -> float:
    return round(size_bytes / (1024 ** 3), 2)


def _slug(title: str, year: int | None) -> str:
    base = slugify(title or "unknown")
    if year:
        return f"{base}-{year}"
    return base


def _requester_watch_status(watch_entry: dict | None, media_type: str) -> dict:
    if not watch_entry:
        return {"watched": False, "completed": False, "completion_pct": 0, "plays": 0, "last_watched": None}

    plays = watch_entry.get("plays", 0)
    pct = watch_entry.get("completion_pct") or 0

    if media_type == "show":
        watched = pct >= 25
        completed = pct >= 80
    else:
        watched = plays >= 1
        completed = plays >= 1

    return {
        "watched": watched,
        "completed": completed,
        "completion_pct": round(pct, 1),
        "plays": plays,
        "last_watched": watch_entry.get("last_watched"),
    }


def build_shows(
    sonarr_items: list[dict],
    tmdb_data: dict,
    overseerr_requests: list[dict],
    tautulli_tv: dict,
    season_watch: dict | None = None,
) -> list[dict]:
    # Index overseerr requests by tmdb_id (most recent wins per item)
    req_by_tmdb: dict[int, dict] = {}
    for r in overseerr_requests:
        if r.get("media_type") == "tv" and r.get("tmdb_id"):
            tid = r["tmdb_id"]
            existing = req_by_tmdb.get(tid)
            if not existing or (r.get("requested_at") or "") > (existing.get("requested_at") or ""):
                req_by_tmdb[tid] = r

    shows = []
    for item in sonarr_items:
        tmdb_id = item.get("tmdb_id")
        meta = tmdb_data.get(tmdb_id, {}) if tmdb_id else {}
        req = req_by_tmdb.get(tmdb_id) if tmdb_id else None

        plex_key = None
        watch_data = {}
        if tmdb_id and tmdb_id in tautulli_tv:
            raw_watch = tautulli_tv[tmdb_id]
            plex_key = raw_watch.get("_plex_key")
            total_eps = item.get("total_episodes") or 1
            for user, wd in raw_watch.items():
                if user.startswith("_"):
                    continue
                unique_eps = wd.get("unique_episodes_watched") or 0
                pct = round(min(100, (unique_eps / total_eps) * 100), 1) if total_eps > 0 else 0
                watch_data[user] = {
                    "plays": wd["plays"],
                    "duration_seconds": wd["duration_seconds"],
                    "last_watched": wd["last_watched"],
                    "completion_pct": pct,
                    "unique_episodes_watched": unique_eps,
                }

        requester_name = req["requester_name"] if req else None
        requester_watch = watch_data.get(requester_name) if requester_name else None
        rws = _requester_watch_status(requester_watch, "show")

        seasons = []
        show_season_watch = (season_watch or {}).get(tmdb_id, {}) if tmdb_id else {}
        for s in item.get("seasons", []):
            snum = s["season_number"]
            total_eps = s["total_episodes"] or 1
            s_watch = show_season_watch.get(snum, {})

            # Compute per-user completion for this season
            s_watch_out: dict[str, dict] = {}
            for user, wd in s_watch.items():
                unique_eps = wd.get("unique_episodes_watched", 0)
                pct = round(min(100, unique_eps / total_eps * 100), 1)
                s_watch_out[user] = {
                    "plays": wd["plays"],
                    "unique_episodes_watched": unique_eps,
                    "completion_pct": pct,
                    "last_watched": wd.get("last_watched"),
                }

            any_season_watched = any(
                w.get("completion_pct", 0) >= 25 for w in s_watch_out.values()
            )
            seasons.append({
                "season_number": snum,
                "monitored": s.get("monitored", True),
                "episode_count": s["episode_count"],
                "total_episodes": s["total_episodes"],
                "size_bytes": s["size_bytes"],
                "size_gb": _gb(s["size_bytes"]),
                "watch_data": s_watch_out,
                "any_watched": any_season_watched,
                "total_plays": sum(w["plays"] for w in s_watch_out.values()),
            })

        shows.append({
            "id": item["id"],
            "slug": _slug(item["title"], item.get("year")),
            "title": item["title"],
            "type": "show",
            "sonarr_id": item["sonarr_id"],
            "sonarr_slug": item.get("sonarr_slug", ""),
            "tmdb_id": tmdb_id,
            "plex_key": plex_key,
            "year": item.get("year"),
            "overview": meta.get("overview") or item.get("overview", ""),
            "genres": meta.get("genres") or item.get("genres", []),
            "rating": meta.get("rating"),
            "poster": meta.get("poster"),
            "backdrop": meta.get("backdrop"),
            "network": item.get("network", ""),
            "status": item.get("status", ""),
            "size_bytes": item["size_bytes"],
            "size_gb": _gb(item["size_bytes"]),
            "episode_count": item.get("episode_count", 0),
            "total_episodes": item.get("total_episodes", 0),
            "quality_profile_id": item.get("quality_profile_id"),
            "seasons": seasons,
            "added_at": item.get("added_at"),
            "request": {
                "requested": req is not None,
                "requester_id": req["requester_id"] if req else None,
                "requester_name": requester_name,
                "requested_at": req["requested_at"] if req else None,
            },
            "watch_data": watch_data,
            "requester_status": rws,
            "total_plays": sum(w["plays"] for w in watch_data.values()),
            "any_watched": any(
                (w.get("completion_pct") or 0) >= 25 for w in watch_data.values()
            ),
        })

    return sorted(shows, key=lambda x: x["size_bytes"], reverse=True)


def build_movies(
    radarr_items: list[dict],
    tmdb_data: dict,
    overseerr_requests: list[dict],
    tautulli_movies: dict,
) -> list[dict]:
    req_by_tmdb: dict[int, dict] = {}
    for r in overseerr_requests:
        if r.get("media_type") == "movie" and r.get("tmdb_id"):
            tid = r["tmdb_id"]
            existing = req_by_tmdb.get(tid)
            if not existing or (r.get("requested_at") or "") > (existing.get("requested_at") or ""):
                req_by_tmdb[tid] = r

    movies = []
    for item in radarr_items:
        if not item.get("has_file"):
            continue
        tmdb_id = item.get("tmdb_id")
        meta = tmdb_data.get(tmdb_id, {}) if tmdb_id else {}
        req = req_by_tmdb.get(tmdb_id) if tmdb_id else None

        plex_key = None
        watch_data = {}
        if tmdb_id and tmdb_id in tautulli_movies:
            raw_watch = tautulli_movies[tmdb_id]
            plex_key = raw_watch.get("_plex_key")
            for user, wd in raw_watch.items():
                if user.startswith("_"):
                    continue
                watch_data[user] = {
                    "plays": wd["plays"],
                    "duration_seconds": wd["duration_seconds"],
                    "last_watched": wd["last_watched"],
                    "completion_pct": wd.get("completion_pct") or (100 if wd["plays"] >= 1 else 0),
                }

        requester_name = req["requester_name"] if req else None
        requester_watch = watch_data.get(requester_name) if requester_name else None
        rws = _requester_watch_status(requester_watch, "movie")

        movies.append({
            "id": item["id"],
            "slug": _slug(item["title"], item.get("year")),
            "title": item["title"],
            "type": "movie",
            "radarr_id": item["radarr_id"],
            "tmdb_id": tmdb_id,
            "plex_key": plex_key,
            "imdb_id": item.get("imdb_id"),
            "year": item.get("year"),
            "overview": meta.get("overview") or item.get("overview", ""),
            "genres": meta.get("genres") or item.get("genres", []),
            "rating": meta.get("rating"),
            "poster": meta.get("poster"),
            "backdrop": meta.get("backdrop"),
            "runtime": meta.get("runtime") or item.get("runtime", 0),
            "certification": item.get("certification", ""),
            "studio": item.get("studio", ""),
            "size_bytes": item["size_bytes"],
            "size_gb": _gb(item["size_bytes"]),
            "quality_profile_id": item.get("quality_profile_id"),
            "added_at": item.get("added_at"),
            "request": {
                "requested": req is not None,
                "requester_id": req["requester_id"] if req else None,
                "requester_name": requester_name,
                "requested_at": req["requested_at"] if req else None,
            },
            "watch_data": watch_data,
            "requester_status": rws,
            "total_plays": sum(w["plays"] for w in watch_data.values()),
            "any_watched": any(w["plays"] >= 1 for w in watch_data.values()),
        })

    return sorted(movies, key=lambda x: x["size_bytes"], reverse=True)
