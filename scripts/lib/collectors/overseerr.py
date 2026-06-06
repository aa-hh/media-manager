import requests
from urllib.parse import urljoin
from .. import log
from ..config import verify_ssl


def _get_all_requests(url: str, api_key: str) -> list[dict]:
    headers = {"X-Api-Key": api_key}
    base = url.rstrip("/") + "/"
    page_size = 100
    skip = 0
    all_results = []
    while True:
        endpoint = urljoin(base, f"api/v1/request?take={page_size}&skip={skip}&sort=added")
        resp = requests.get(endpoint, headers=headers, timeout=30, verify=verify_ssl())
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        all_results.extend(results)
        page_info = data.get("pageInfo", {})
        total = page_info.get("results", 0)
        skip += page_size
        if skip >= total:
            break
    return all_results


def _get_users(url: str, api_key: str) -> dict[int, dict]:
    headers = {"X-Api-Key": api_key}
    endpoint = urljoin(url.rstrip("/") + "/", "api/v1/user?take=100&skip=0")
    resp = requests.get(endpoint, headers=headers, timeout=30, verify=verify_ssl())
    resp.raise_for_status()
    data = resp.json()
    users = {}
    for u in data.get("results", []):
        users[u["id"]] = {
            "id": u["id"],
            "name": u.get("displayName") or u.get("username") or f"user_{u['id']}",
            "email": u.get("email", ""),
            "avatar": u.get("avatar", ""),
            "request_count": u.get("requestCount", 0),
        }
    return users


def fetch(url: str, api_key: str) -> tuple[list[dict], dict[int, dict]]:
    """Returns (requests_list, users_by_id)."""
    users = _get_users(url, api_key)
    log.info(f"Overseerr: fetched {len(users)} users")

    raw_requests = _get_all_requests(url, api_key)
    log.info(f"Overseerr: fetched {len(raw_requests)} requests")

    requests_out = []
    for r in raw_requests:
        media = r.get("media", {})
        requested_by = r.get("requestedBy", {})
        requester_id = requested_by.get("id")
        requester_name = None
        if requester_id and requester_id in users:
            requester_name = users[requester_id]["name"]
        elif requested_by:
            requester_name = requested_by.get("displayName") or requested_by.get("username")

        media_type = r.get("type")  # "tv" or "movie"
        tmdb_id = media.get("tmdbId")

        requests_out.append({
            "request_id": r.get("id"),
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "tvdb_id": media.get("tvdbId"),
            "requester_id": requester_id,
            "requester_name": requester_name,
            "requested_at": r.get("createdAt"),
            "status": r.get("status"),
            "seasons": [s.get("seasonNumber") for s in r.get("seasons", [])],
        })

    return requests_out, users


def fetch_watchlist(url: str, api_key: str, users: dict[int, dict]) -> dict[tuple, set[int]]:
    """
    Returns a dict mapping (media_type, tmdb_id) -> set of overseerr user_ids who have
    that item on their watchlist. media_type is "tv" or "movie". Keeping the per-user
    attribution lets callers tell whether a *specific* user (e.g. the requester) is the
    one waiting on it, rather than just whether it's on someone's watchlist.
    """
    headers = {"X-Api-Key": api_key}
    base = url.rstrip("/") + "/"
    watchlist: dict[tuple, set[int]] = {}

    for user_id in users:
        page = 1
        while True:
            endpoint = urljoin(base, f"api/v1/user/{user_id}/watchlist?page={page}")
            try:
                resp = requests.get(endpoint, headers=headers, timeout=30, verify=verify_ssl())
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.warn(f"Overseerr watchlist fetch failed for user {user_id}: {e}")
                break
            for item in data.get("results", []):
                media_type = item.get("mediaType")
                tmdb_id = item.get("tmdbId")
                if media_type and tmdb_id:
                    watchlist.setdefault((media_type, int(tmdb_id)), set()).add(user_id)
            if page >= data.get("totalPages", 1):
                break
            page += 1

    log.info(f"Overseerr: fetched {len(watchlist)} watchlist items across {len(users)} users")
    return watchlist
