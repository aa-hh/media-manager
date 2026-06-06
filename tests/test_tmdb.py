import json

from lib.collectors import tmdb


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ── _img ──────────────────────────────────────────────────────────────────────

def test_img_returns_none_for_missing_path():
    assert tmdb._img(None, "w342") is None
    assert tmdb._img("", "w342") is None


def test_img_builds_url():
    assert tmdb._img("/abc.jpg", "w342") == "https://image.tmdb.org/t/p/w342/abc.jpg"


# ── _fetch_one ────────────────────────────────────────────────────────────────

def test_fetch_one_returns_none_on_404(mocker):
    mocker.patch.object(tmdb.requests, "get", return_value=_FakeResponse({}, status_code=404))
    assert tmdb._fetch_one(1, "movie", "key") is None


def test_fetch_one_returns_none_on_exception(mocker):
    mocker.patch.object(tmdb.requests, "get", side_effect=RuntimeError("boom"))
    assert tmdb._fetch_one(1, "movie", "key") is None


def test_fetch_one_normalises_movie_data(mocker):
    data = {
        "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
        "vote_average": 7.5, "vote_count": 100,
        "genres": [{"name": "Action"}, {"name": "Drama"}],
        "overview": "desc", "runtime": 120, "status": "Released",
    }
    mocker.patch.object(tmdb.requests, "get", return_value=_FakeResponse(data))
    result = tmdb._fetch_one(42, "movie", "key")
    assert result["tmdb_id"] == 42
    assert result["poster"] == "https://image.tmdb.org/t/p/w342/p.jpg"
    assert result["backdrop"] == "https://image.tmdb.org/t/p/w1280/b.jpg"
    assert result["genres"] == ["Action", "Drama"]
    assert result["runtime"] == 120


def test_fetch_one_falls_back_to_episode_run_time_for_tv():
    data = {"episode_run_time": [45, 50], "genres": []}

    def fake_get(*args, **kwargs):
        return _FakeResponse(data)

    import lib.collectors.tmdb as tmdb_mod
    orig = tmdb_mod.requests.get
    tmdb_mod.requests.get = fake_get
    try:
        result = tmdb._fetch_one(1, "tv", "key")
        assert result["runtime"] == 45
    finally:
        tmdb_mod.requests.get = orig


# ── enrich ────────────────────────────────────────────────────────────────────

def test_enrich_uses_cache_without_fetching(tmp_path, mocker):
    cache_file = tmp_path / "tmdb_cache.json"
    cache_file.write_text(json.dumps({"100": {"tmdb_id": 100, "rating": 8.0}}))
    get_mock = mocker.patch.object(tmdb.requests, "get")

    items = [{"tmdb_id": 100}]
    result = tmdb.enrich(items, "movie", "key", cache_file)

    assert result == {100: {"tmdb_id": 100, "rating": 8.0}}
    get_mock.assert_not_called()


def test_enrich_fetches_missing_items_and_writes_cache(tmp_path, mocker):
    cache_file = tmp_path / "tmdb_cache.json"
    cache_file.write_text(json.dumps({}))

    fetched = {"tmdb_id": 200, "poster": None, "backdrop": None, "rating": 6.5,
               "vote_count": 10, "genres": [], "overview": "", "runtime": None, "status": ""}
    mocker.patch.object(tmdb, "_fetch_one", return_value=fetched)

    items = [{"tmdb_id": 200}, {"tmdb_id": None}]
    result = tmdb.enrich(items, "movie", "key", cache_file)

    assert result == {200: fetched}
    saved = json.loads(cache_file.read_text())
    assert "200" in saved


def test_enrich_handles_corrupt_cache_file(tmp_path, mocker):
    cache_file = tmp_path / "tmdb_cache.json"
    cache_file.write_text("not json")
    mocker.patch.object(tmdb, "_fetch_one", return_value=None)

    result = tmdb.enrich([{"tmdb_id": 1}], "movie", "key", cache_file)
    assert result == {}


def test_enrich_dedupes_needed_ids(tmp_path, mocker):
    cache_file = tmp_path / "tmdb_cache.json"
    cache_file.write_text(json.dumps({}))
    fetch_mock = mocker.patch.object(tmdb, "_fetch_one", return_value=None)

    items = [{"tmdb_id": 5}, {"tmdb_id": 5}, {"tmdb_id": 5}]
    tmdb.enrich(items, "movie", "key", cache_file)
    assert fetch_mock.call_count == 1
