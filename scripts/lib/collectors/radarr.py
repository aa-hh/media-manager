import requests
from urllib.parse import urljoin
from .. import log
from ..config import verify_ssl


def _normalise_codec(raw: str) -> str:
    r = (raw or "").lower().replace(" ", "")
    if r in ("x264", "h264", "avc"):
        return "H.264"
    if r in ("x265", "h265", "hevc"):
        return "H.265"
    if r == "av1":
        return "AV1"
    if r == "vp9":
        return "VP9"
    if r in ("vc1", "vc-1", "wmv3"):
        return "VC-1"
    if r in ("mpeg2video", "mpeg2"):
        return "MPEG-2"
    if r == "xvid":
        return "XviD"
    return raw.upper() if raw else ""


def _fmt_resolution(res) -> str:
    """Accept int pixels or 'WxH' string."""
    if not res:
        return ""
    try:
        if isinstance(res, str) and "x" in res:
            res = int(res.split("x")[1])  # height from "1920x1080"
        else:
            res = int(res)
    except (ValueError, TypeError):
        return str(res)
    if res >= 2160:
        return "4K"
    if res >= 1080:
        return "1080p"
    if res >= 720:
        return "720p"
    if res >= 480:
        return "480p"
    return f"{res}p"


def _is_hdr(media: dict) -> bool:
    primaries = (media.get("videoColourPrimaries") or "").lower()
    hdr_fmt   = (media.get("videoHdrFormat") or "").lower()
    dynamic   = (media.get("videoDynamicRange") or "").lower()
    return "bt2020" in primaries or bool(hdr_fmt) or "hdr" in dynamic


def _extract_file_info(movie: dict) -> dict:
    """Extract codec/resolution from the movieFile sub-object included inline by Radarr."""
    mf      = movie.get("movieFile") or {}
    quality = (mf.get("quality") or {}).get("quality") or {}
    media   = mf.get("mediaInfo") or {}
    codec   = _normalise_codec(media.get("videoCodec", ""))
    # resolution: prefer quality int, fall back to mediaInfo 'WxH' string
    res = _fmt_resolution(quality.get("resolution") or media.get("resolution"))
    return {
        "video_codec":  codec,
        "audio_codec":  media.get("audioCodec", ""),
        "resolution":   res,
        "bit_depth":    media.get("videoBitDepth"),
        "hdr":          _is_hdr(media),
        "quality_name": quality.get("name", ""),
    }


def fetch(url: str, api_key: str) -> list[dict]:
    headers = {"X-Api-Key": api_key}
    endpoint = urljoin(url.rstrip("/") + "/", "api/v3/movie")
    resp = requests.get(endpoint, headers=headers, timeout=60, verify=verify_ssl())
    resp.raise_for_status()
    movie_list = resp.json()
    log.info(f"Radarr: fetched {len(movie_list)} movies")

    items = []
    for m in movie_list:
        mid = m.get("id")
        fi  = _extract_file_info(m) if m.get("hasFile") else {}
        items.append({
            "radarr_id":        mid,
            "id":               f"movie:{mid}",
            "title":            m.get("title"),
            "overview":         m.get("overview", ""),
            "year":             m.get("year"),
            "tmdb_id":          m.get("tmdbId"),
            "imdb_id":          m.get("imdbId"),
            "path":             m.get("path"),
            "quality_profile_id": m.get("qualityProfileId"),
            "size_bytes":       m.get("sizeOnDisk", 0),
            "has_file":         m.get("hasFile", False),
            "genres":           m.get("genres", []),
            "studio":           m.get("studio", ""),
            "runtime":          m.get("runtime", 0),
            "certification":    m.get("certification", ""),
            "added_at":         m.get("added"),
            "video_codec":      fi.get("video_codec", ""),
            "audio_codec":      fi.get("audio_codec", ""),
            "resolution":       fi.get("resolution", ""),
            "bit_depth":        fi.get("bit_depth"),
            "hdr":              fi.get("hdr", False),
            "quality_name":     fi.get("quality_name", ""),
        })

    return items
