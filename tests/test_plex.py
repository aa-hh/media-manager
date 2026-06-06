from xml.etree import ElementTree as ET

import pytest

from lib.collectors import plex


class _FakeResponse:
    def __init__(self, xml_str, status_ok=True):
        self.content = xml_str.encode("utf-8")
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")


# ── _get ──────────────────────────────────────────────────────────────────────

def test_get_parses_xml(mocker):
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse("<MediaContainer size='1'></MediaContainer>"))
    root = plex._get("http://plex.local", "tok", "/accounts")
    assert root.tag == "MediaContainer"


def test_get_disables_warnings_when_ssl_off(mocker):
    mocker.patch.object(plex, "verify_ssl", return_value=False)
    get_mock = mocker.patch.object(plex.requests, "get", return_value=_FakeResponse("<a/>"))
    plex._get("http://plex.local", "tok", "/accounts")
    assert get_mock.call_args.kwargs["verify"] is False


# ── _get_accounts ─────────────────────────────────────────────────────────────

def test_get_accounts_filters_admin_and_unnamed(mocker):
    xml = """<MediaContainer>
        <Account id="0" name="admin"/>
        <Account id="1" name="alice"/>
        <Account id="2" name=""/>
    </MediaContainer>"""
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse(xml))
    accounts = plex._get_accounts("http://plex.local", "tok")
    assert accounts == [{"id": 1, "name": "alice"}]


# ── _extract_tmdb_id ──────────────────────────────────────────────────────────

def test_extract_tmdb_id_finds_guid():
    el = ET.fromstring('<Video><Guid id="imdb://tt1"/><Guid id="tmdb://123?lang=en"/></Video>')
    assert plex._extract_tmdb_id(el) == 123


def test_extract_tmdb_id_returns_none_when_absent():
    el = ET.fromstring('<Video><Guid id="imdb://tt1"/></Video>')
    assert plex._extract_tmdb_id(el) is None


def test_extract_tmdb_id_handles_malformed_guid():
    el = ET.fromstring('<Video><Guid id="tmdb://notanumber"/></Video>')
    assert plex._extract_tmdb_id(el) is None


# ── _get_library_metadata ─────────────────────────────────────────────────────

def test_get_library_metadata_parses_items_and_skips_incomplete(mocker):
    xml = """<MediaContainer>
        <Video title="Show A" ratingKey="100" leafCount="10">
            <Guid id="tmdb://200"/>
        </Video>
        <Video title="" ratingKey="101"/>
        <Video title="No Key"/>
    </MediaContainer>"""
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse(xml))
    items = plex._get_library_metadata("http://plex.local", "tok", "1")
    assert items == {"Show A": {"rating_key": "100", "tmdb_id": 200, "leaf_count": 10}}


def test_get_library_metadata_falls_back_to_individual_request(mocker):
    list_xml = """<MediaContainer>
        <Video title="Show A" ratingKey="100" leafCount="10"/>
    </MediaContainer>"""
    detail_xml = """<MediaContainer>
        <Video><Guid id="tmdb://321"/></Video>
    </MediaContainer>"""
    mocker.patch.object(plex.requests, "get", side_effect=[_FakeResponse(list_xml), _FakeResponse(detail_xml)])
    items = plex._get_library_metadata("http://plex.local", "tok", "1")
    assert items["Show A"]["tmdb_id"] == 321


def test_get_library_metadata_fallback_swallows_errors(mocker):
    list_xml = """<MediaContainer>
        <Video title="Show A" ratingKey="100" leafCount="10"/>
    </MediaContainer>"""
    mocker.patch.object(plex.requests, "get", side_effect=[_FakeResponse(list_xml), RuntimeError("boom")])
    items = plex._get_library_metadata("http://plex.local", "tok", "1")
    assert items["Show A"]["tmdb_id"] is None


# ── _fetch_history_page / _fetch_all_history ─────────────────────────────────

def test_fetch_history_page_parses_entries_and_total(mocker):
    xml = '<MediaContainer totalSize="2"><Video ratingKey="1" viewedAt="100"/><Video ratingKey="2" viewedAt="200"/></MediaContainer>'
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse(xml))
    entries, total = plex._fetch_history_page("http://plex.local", "tok", 1, "1", 0)
    assert total == 2
    assert entries == [{"ratingKey": "1", "viewedAt": "100"}, {"ratingKey": "2", "viewedAt": "200"}]


def test_fetch_all_history_returns_empty_when_total_zero(mocker):
    mocker.patch.object(plex, "_fetch_history_page", return_value=([], 0))
    assert plex._fetch_all_history("http://plex.local", "tok", 1, "1") == []


def test_fetch_all_history_paginates(mocker):
    page1 = ([{"ratingKey": "1"}] * plex.PAGE_SIZE, plex.PAGE_SIZE + 1)
    page2 = ([{"ratingKey": "2"}], plex.PAGE_SIZE + 1)
    mocker.patch.object(plex, "_fetch_history_page", side_effect=[page1, page1, page2])
    entries = plex._fetch_all_history("http://plex.local", "tok", 1, "1")
    assert len(entries) == plex.PAGE_SIZE + 1


# ── get_library_sections ──────────────────────────────────────────────────────

def test_get_library_sections_parses_directories(mocker):
    xml = """<MediaContainer>
        <Directory key="1" title="TV Shows" type="show"/>
        <Directory key="2" title="Movies" type="movie"/>
        <Directory title="No Key"/>
    </MediaContainer>"""
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse(xml))
    sections = plex.get_library_sections("http://plex.local", "tok")
    assert sections == [
        {"id": "1", "title": "TV Shows", "type": "show"},
        {"id": "2", "title": "Movies", "type": "movie"},
    ]


# ── _get_machine_id ───────────────────────────────────────────────────────────

def test_get_machine_id_returns_identifier(mocker):
    xml = '<MediaContainer machineIdentifier="abc123"/>'
    mocker.patch.object(plex.requests, "get", return_value=_FakeResponse(xml))
    assert plex._get_machine_id("http://plex.local", "tok") == "abc123"


def test_get_machine_id_returns_empty_on_error(mocker):
    mocker.patch.object(plex.requests, "get", side_effect=RuntimeError("boom"))
    assert plex._get_machine_id("http://plex.local", "tok") == ""


# ── fetch (full integration of the aggregation logic) ────────────────────────

@pytest.fixture
def fetch_mocks(mocker):
    mocker.patch.object(plex, "_get_machine_id", return_value="machine-1")
    mocker.patch.object(plex, "_get_accounts", return_value=[{"id": 1, "name": "alice"}])
    mocker.patch.object(plex, "_get_library_metadata", side_effect=[
        {"Show A": {"rating_key": "100", "tmdb_id": 200, "leaf_count": 2}},
        {"Movie A": {"rating_key": "300", "tmdb_id": 400, "leaf_count": 0}},
    ])

    tv_history = [
        {"grandparentTitle": "Show A", "parentIndex": "1", "index": "1", "viewedAt": "1000"},
        {"grandparentTitle": "Show A", "parentIndex": "1", "index": "2", "viewedAt": "2000"},
        {"grandparentTitle": "Unknown Show", "parentIndex": "1", "index": "1", "viewedAt": "3000"},
    ]
    movie_history = [
        {"title": "Movie A", "viewedAt": "5000"},
        {"title": "Movie A", "viewedAt": "6000"},
        {"title": "Unknown Movie", "viewedAt": "7000"},
    ]
    mocker.patch.object(plex, "_fetch_all_history", side_effect=[tv_history, movie_history])
    return mocker


def test_fetch_aggregates_tv_and_movie_watch_data(fetch_mocks):
    result = plex.fetch("http://plex.local", "tok", name_map={"alice": "Alice"})

    assert result["machine_id"] == "machine-1"
    assert result["users"] == [{"user_id": 1, "friendly_name": "Alice"}]

    assert 200 in result["tv"]
    show_rec = result["tv"][200]["Alice"]
    assert show_rec["plays"] == 2
    assert show_rec["unique_episodes_watched"] == 2
    assert show_rec["completion_pct"] == 100.0
    assert show_rec["last_watched"] is not None

    season_rec = result["tv_seasons"][200][1]["Alice"]
    assert season_rec["plays"] == 2
    assert season_rec["unique_episodes_watched"] == 2

    movie_rec = result["movie"][400]["Alice"]
    assert movie_rec["plays"] == 2
    assert movie_rec["completion_pct"] == 100

    # Unknown show/movie titles without metadata are skipped
    assert all(v.get("_plex_key") != "Unknown Show" for v in result["tv"].values())


def test_fetch_uses_default_section_ids(mocker):
    mocker.patch.object(plex, "_get_machine_id", return_value="")
    mocker.patch.object(plex, "_get_accounts", return_value=[])
    meta_mock = mocker.patch.object(plex, "_get_library_metadata", return_value={})
    history_mock = mocker.patch.object(plex, "_fetch_all_history", return_value=[])

    result = plex.fetch("http://plex.local", "tok")

    assert result["users"] == []
    assert meta_mock.call_count == 2  # one TV section, one movie section
    assert history_mock.call_count == 0  # no accounts to iterate
