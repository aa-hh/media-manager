import requests
from urllib.parse import urljoin
from .. import log


def fetch(url: str, api_key: str) -> list[dict]:
    headers = {"X-Api-Key": api_key}
    endpoint = urljoin(url.rstrip("/") + "/", "api/v3/movie")
    resp = requests.get(endpoint, headers=headers, timeout=60)
    resp.raise_for_status()
    movie_list = resp.json()
    log.info(f"Radarr: fetched {len(movie_list)} movies")

    items = []
    for m in movie_list:
        mid = m.get("id")
        items.append({
            "radarr_id": mid,
            "id": f"movie:{mid}",
            "title": m.get("title"),
            "overview": m.get("overview", ""),
            "year": m.get("year"),
            "tmdb_id": m.get("tmdbId"),
            "imdb_id": m.get("imdbId"),
            "path": m.get("path"),
            "quality_profile_id": m.get("qualityProfileId"),
            "size_bytes": m.get("sizeOnDisk", 0),
            "has_file": m.get("hasFile", False),
            "genres": m.get("genres", []),
            "studio": m.get("studio", ""),
            "runtime": m.get("runtime", 0),
            "certification": m.get("certification", ""),
            "added_at": m.get("added"),
        })

    return items
