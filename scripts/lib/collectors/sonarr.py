import requests
from urllib.parse import urljoin
from .. import log
from ..config import verify_ssl


def _fetch_quality_profiles(url: str, api_key: str) -> dict[int, str]:
    """Returns {profile_id: profile_name}."""
    try:
        resp = requests.get(f"{url.rstrip('/')}/api/v3/qualityprofile",
                            headers={"X-Api-Key": api_key}, timeout=30, verify=verify_ssl())
        resp.raise_for_status()
        return {p["id"]: p["name"] for p in resp.json()}
    except Exception as e:
        log.warn(f"Sonarr: could not fetch quality profiles: {e}")
        return {}


def fetch(url: str, api_key: str) -> list[dict]:
    headers = {"X-Api-Key": api_key}
    quality_profiles = _fetch_quality_profiles(url, api_key)
    endpoint = urljoin(url.rstrip("/") + "/", "api/v3/series")
    resp = requests.get(endpoint, headers=headers, timeout=60, verify=verify_ssl())
    resp.raise_for_status()
    series_list = resp.json()
    log.info(f"Sonarr: fetched {len(series_list)} series")

    items = []
    for s in series_list:
        sid = s.get("id")
        stats = s.get("statistics", {})

        seasons = []
        for season in s.get("seasons", []):
            sn = season.get("seasonNumber", 0)
            if sn == 0:
                continue
            ss = season.get("statistics", {})
            seasons.append({
                "season_number": sn,
                "monitored": season.get("monitored", False),
                "episode_count": ss.get("episodeFileCount", 0),
                "total_episodes": ss.get("totalEpisodeCount", 0),
                "size_bytes": ss.get("sizeOnDisk", 0),
            })

        items.append({
            "sonarr_id": sid,
            "id": f"tv:{sid}",
            "title": s.get("title"),
            "overview": s.get("overview", ""),
            "status": s.get("status"),
            "year": s.get("year"),
            "tmdb_id": s.get("tmdbId"),
            "tvdb_id": s.get("tvdbId"),
            "path": s.get("path"),
            "quality_profile_id":   s.get("qualityProfileId"),
            "quality_profile_name": quality_profiles.get(s.get("qualityProfileId"), ""),
            "size_bytes": stats.get("sizeOnDisk", 0),
            "episode_count": stats.get("episodeFileCount", 0),
            "total_episodes": stats.get("totalEpisodeCount", 0),
            "seasons": seasons,
            "sonarr_slug": s.get("titleSlug"),
            "network": s.get("network", ""),
            "genres": s.get("genres", []),
            "added_at": s.get("added"),
        })

    return items
