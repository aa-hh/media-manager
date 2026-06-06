import pytest

from lib.collectors import sonarr, radarr, overseerr


class _FakeResponse:
    def __init__(self, json_data, status_ok=True):
        self._json = json_data
        self._status_ok = status_ok

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")


# ── radarr pure helpers ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("x264", "H.264"), ("h264", "H.264"), ("AVC", "H.264"),
    ("x265", "H.265"), ("hevc", "H.265"),
    ("av1", "AV1"), ("vp9", "VP9"),
    ("vc1", "VC-1"), ("wmv3", "VC-1"),
    ("mpeg2video", "MPEG-2"),
    ("xvid", "XviD"),
    ("somethingelse", "SOMETHINGELSE"),
    ("", ""),
    (None, ""),
])
def test_normalise_codec(raw, expected):
    assert radarr._normalise_codec(raw) == expected


@pytest.mark.parametrize("res,expected", [
    (None, ""),
    (0, ""),
    (2160, "4K"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
    ("1920x1080", "1080p"),
    ("3840x2160", "4K"),
    ("not-a-resolution", "not-a-resolution"),
])
def test_fmt_resolution(res, expected):
    assert radarr._fmt_resolution(res) == expected


def test_is_hdr_detection():
    assert radarr._is_hdr({"videoColourPrimaries": "BT2020"}) is True
    assert radarr._is_hdr({"videoHdrFormat": "Dolby Vision"}) is True
    assert radarr._is_hdr({"videoDynamicRange": "HDR"}) is True
    assert radarr._is_hdr({}) is False
    assert radarr._is_hdr({"videoColourPrimaries": "bt709"}) is False


def test_extract_file_info_prefers_quality_resolution():
    movie = {
        "movieFile": {
            "quality": {"quality": {"resolution": 1080, "name": "Bluray-1080p"}},
            "mediaInfo": {
                "videoCodec": "x265", "audioCodec": "EAC3",
                "resolution": "3840x2160", "videoBitDepth": 10,
                "videoColourPrimaries": "bt2020",
            },
        }
    }
    fi = radarr._extract_file_info(movie)
    assert fi["video_codec"] == "H.265"
    assert fi["resolution"] == "1080p"  # quality.resolution wins over mediaInfo
    assert fi["audio_codec"] == "EAC3"
    assert fi["bit_depth"] == 10
    assert fi["hdr"] is True
    assert fi["quality_name"] == "Bluray-1080p"


def test_extract_file_info_falls_back_to_media_info_resolution():
    movie = {"movieFile": {"quality": {"quality": {}}, "mediaInfo": {"resolution": "1280x720"}}}
    fi = radarr._extract_file_info(movie)
    assert fi["resolution"] == "720p"


def test_extract_file_info_handles_missing_movie_file():
    assert radarr._extract_file_info({}) == {
        "video_codec": "", "audio_codec": "", "resolution": "",
        "bit_depth": None, "hdr": False, "quality_name": "",
    }


# ── radarr.fetch ──────────────────────────────────────────────────────────────

def test_radarr_fetch_normalises_items(mocker):
    movies = [
        {
            "id": 1, "title": "Movie A", "year": 2020, "tmdbId": 100, "imdbId": "tt1",
            "hasFile": True, "sizeOnDisk": 5000, "genres": ["Action"],
            "movieFile": {
                "quality": {"quality": {"resolution": 1080, "name": "WEBDL-1080p"}},
                "mediaInfo": {"videoCodec": "h264", "audioCodec": "AAC", "videoBitDepth": 8},
            },
        },
        {"id": 2, "title": "Movie B (no file)", "year": 2021, "tmdbId": 101, "hasFile": False, "sizeOnDisk": 0},
    ]
    mocker.patch.object(radarr.requests, "get", return_value=_FakeResponse(movies))

    items = radarr.fetch("http://radarr.local", "key")
    assert len(items) == 2
    a, b = items
    assert a["id"] == "movie:1"
    assert a["video_codec"] == "H.264"
    assert a["resolution"] == "1080p"
    assert a["has_file"] is True
    assert b["has_file"] is False
    assert b["video_codec"] == ""


# ── sonarr.fetch ──────────────────────────────────────────────────────────────

def test_sonarr_fetch_builds_items_with_quality_profiles_and_seasons(mocker):
    quality_profiles_resp = _FakeResponse([{"id": 7, "name": "HD-1080p"}])
    series_resp = _FakeResponse([
        {
            "id": 50, "title": "Show A", "overview": "ov", "status": "continuing",
            "year": 2019, "tmdbId": 200, "tvdbId": 300, "qualityProfileId": 7,
            "statistics": {"sizeOnDisk": 9000, "episodeFileCount": 10, "totalEpisodeCount": 12},
            "titleSlug": "show-a", "network": "NBC", "genres": ["Drama"], "added": "2024-01-01",
            "seasons": [
                {"seasonNumber": 0, "monitored": False, "statistics": {}},  # specials, skipped
                {"seasonNumber": 1, "monitored": True,
                 "statistics": {"episodeFileCount": 5, "totalEpisodeCount": 6, "sizeOnDisk": 4000}},
            ],
        }
    ])
    mocker.patch.object(sonarr.requests, "get", side_effect=[quality_profiles_resp, series_resp])

    items = sonarr.fetch("http://sonarr.local", "key")
    assert len(items) == 1
    show = items[0]
    assert show["id"] == "tv:50"
    assert show["quality_profile_name"] == "HD-1080p"
    assert len(show["seasons"]) == 1  # season 0 skipped
    assert show["seasons"][0]["season_number"] == 1
    assert show["seasons"][0]["episode_count"] == 5


def test_sonarr_fetch_quality_profiles_failure_returns_empty_dict(mocker):
    mocker.patch.object(sonarr.requests, "get", side_effect=RuntimeError("boom"))
    profiles = sonarr._fetch_quality_profiles("http://sonarr.local", "key")
    assert profiles == {}


# ── overseerr ─────────────────────────────────────────────────────────────────

def test_overseerr_get_users_normalises_records(mocker):
    resp = _FakeResponse({"results": [
        {"id": 1, "displayName": "Alice", "email": "a@x.com", "requestCount": 5},
        {"id": 2, "username": "bob_user", "requestCount": 0},
        {"id": 3},
    ]})
    mocker.patch.object(overseerr.requests, "get", return_value=resp)
    users = overseerr._get_users("http://seerr.local", "key")
    assert users[1]["name"] == "Alice"
    assert users[2]["name"] == "bob_user"
    assert users[3]["name"] == "user_3"


def test_overseerr_get_all_requests_paginates(mocker):
    page1 = _FakeResponse({"results": [{"id": 1}, {"id": 2}], "pageInfo": {"results": 150}})
    page2 = _FakeResponse({"results": [{"id": 3}], "pageInfo": {"results": 150}})
    mocker.patch.object(overseerr.requests, "get", side_effect=[page1, page2])
    results = overseerr._get_all_requests("http://seerr.local", "key")
    assert [r["id"] for r in results] == [1, 2, 3]


def test_overseerr_fetch_resolves_requester_names(mocker):
    users_resp = _FakeResponse({"results": [{"id": 1, "displayName": "Alice"}]})
    requests_resp = _FakeResponse({"results": [
        {"id": 10, "type": "tv", "media": {"tmdbId": 555, "tvdbId": 999},
         "requestedBy": {"id": 1}, "createdAt": "2024-01-01", "status": 2,
         "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}]},
        {"id": 11, "type": "movie", "media": {"tmdbId": 777},
         "requestedBy": {"id": 99, "displayName": "External User"}, "createdAt": "2024-02-01"},
    ], "pageInfo": {"results": 2}})
    mocker.patch.object(overseerr.requests, "get", side_effect=[users_resp, requests_resp])

    results, users = overseerr.fetch("http://seerr.local", "key")
    assert results[0]["requester_name"] == "Alice"
    assert results[0]["seasons"] == [1, 2]
    assert results[1]["requester_name"] == "External User"
    assert users[1]["name"] == "Alice"


def test_overseerr_fetch_watchlist_groups_by_media_type_and_tmdb_id(mocker):
    users = {1: {"id": 1, "name": "Alice"}}
    page = _FakeResponse({
        "results": [
            {"mediaType": "tv", "tmdbId": 100},
            {"mediaType": "movie", "tmdbId": 200},
            {"mediaType": None, "tmdbId": 300},
        ],
        "totalPages": 1,
    })
    mocker.patch.object(overseerr.requests, "get", return_value=page)
    watchlist = overseerr.fetch_watchlist("http://seerr.local", "key", users)
    assert watchlist[("tv", 100)] == {1}
    assert watchlist[("movie", 200)] == {1}
    assert ("tv", 300) not in watchlist and (None, 300) not in watchlist


def test_overseerr_fetch_watchlist_handles_request_failure_gracefully(mocker):
    users = {1: {"id": 1, "name": "Alice"}}
    mocker.patch.object(overseerr.requests, "get", side_effect=RuntimeError("network error"))
    watchlist = overseerr.fetch_watchlist("http://seerr.local", "key", users)
    assert watchlist == {}
