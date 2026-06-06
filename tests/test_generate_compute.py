import sqlite3

import pytest

from generate import _compute_format_metrics, _compute_user_bandwidth, _compute_playback_analytics


PLAYS_COLUMNS = [
    "event", "transcode_decision", "video_decision", "audio_decision", "subtitle_decision",
    "quality_profile", "src_video_codec", "src_video_resolution", "src_hdr_type",
    "src_audio_codec", "src_audio_channels", "stream_video_codec", "stream_video_resolution",
    "client_platform", "client_friendly_name", "video_decision_dup", "stream_video_bitrate",
    "src_video_bitrate", "tmdb_id", "media_type",
]


@pytest.fixture
def db_factory(tmp_path):
    """Return a function that builds a webhook_plays.db with given row dicts and returns its path."""
    def _build(rows):
        db_path = tmp_path / "webhook_plays.db"
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE plays (
                event TEXT, transcode_decision TEXT, video_decision TEXT, audio_decision TEXT,
                subtitle_decision TEXT, quality_profile TEXT, src_video_codec TEXT,
                src_video_resolution TEXT, src_hdr_type TEXT, src_audio_codec TEXT,
                src_audio_channels TEXT, stream_video_codec TEXT, stream_video_resolution TEXT,
                client_platform TEXT, client_friendly_name TEXT,
                stream_video_bitrate TEXT, src_video_bitrate TEXT,
                tmdb_id INTEGER, media_type TEXT
            )
        """)
        for row in rows:
            defaults = {
                "event": "play", "transcode_decision": None, "video_decision": None,
                "audio_decision": None, "subtitle_decision": None, "quality_profile": None,
                "src_video_codec": None, "src_video_resolution": None, "src_hdr_type": None,
                "src_audio_codec": None, "src_audio_channels": None, "stream_video_codec": None,
                "stream_video_resolution": None, "client_platform": None, "client_friendly_name": None,
                "stream_video_bitrate": None, "src_video_bitrate": None,
                "tmdb_id": None, "media_type": None,
            }
            defaults.update(row)
            con.execute(
                f"INSERT INTO plays ({', '.join(defaults)}) VALUES ({', '.join('?' for _ in defaults)})",
                list(defaults.values()),
            )
        con.commit()
        con.close()
        return db_path
    return _build


# ── _compute_format_metrics ───────────────────────────────────────────────────

def test_compute_format_metrics_missing_db_returns_empty(tmp_path):
    assert _compute_format_metrics(tmp_path / "missing.db") == {}


def test_compute_format_metrics_empty_table_returns_empty(db_factory):
    db_path = db_factory([])
    assert _compute_format_metrics(db_path) == {}


def test_compute_format_metrics_groups_by_codec_resolution_and_hdr(db_factory):
    db_path = db_factory([
        {"src_video_codec": "hevc", "src_video_resolution": "2160p", "src_hdr_type": "HDR10",
         "video_decision": "direct play", "stream_video_resolution": "2160p"},
        {"src_video_codec": "h264", "src_video_resolution": "1080p", "src_hdr_type": "",
         "video_decision": "transcode", "stream_video_resolution": "720p"},
        {"src_video_codec": "h264", "src_video_resolution": "1080p", "src_hdr_type": "",
         "video_decision": "copy", "stream_video_resolution": "1080p"},
    ])
    result = _compute_format_metrics(db_path)
    formats = {r["format"]: r for r in result["rows"]}
    assert "H.265 4K HDR10" in formats
    assert "H.264 1080p" in formats
    h264 = formats["H.264 1080p"]
    assert h264["total_plays"] == 2
    assert h264["transcode_pct"] == 50
    assert h264["copy_pct"] == 50
    assert h264["quality_pcts"]["720p"] == 50
    assert h264["quality_pcts"]["1080p"] == 50


def test_compute_format_metrics_codec_label_variants(db_factory):
    db_path = db_factory([
        {"src_video_codec": "av1", "video_decision": "direct play"},
        {"src_video_codec": "vp9", "video_decision": "direct play"},
        {"src_video_codec": "mpeg2video", "video_decision": "direct play"},
        {"src_video_codec": "vc1", "video_decision": "direct play"},
        {"src_video_codec": "weirdcodec", "video_decision": "direct play"},
        {"src_video_codec": None, "video_decision": "direct play"},
    ])
    result = _compute_format_metrics(db_path)
    labels = {r["format"] for r in result["rows"]}
    assert "AV1" in labels
    assert "VP9" in labels
    assert "MPEG-2" in labels
    assert "VC-1" in labels
    assert "WEIRDCODEC" in labels
    assert "Unknown" in labels


def test_compute_format_metrics_skips_unknown_decisions(db_factory):
    db_path = db_factory([
        {"src_video_codec": "h264", "src_video_resolution": "1080p", "video_decision": "weird"},
    ])
    result = _compute_format_metrics(db_path)
    assert result["rows"] == []


def test_compute_format_metrics_quality_profiles_sorted_descending(db_factory):
    db_path = db_factory([
        {"src_video_codec": "h264", "video_decision": "transcode", "stream_video_resolution": "480p"},
        {"src_video_codec": "h264", "video_decision": "transcode", "stream_video_resolution": "1080p"},
        {"src_video_codec": "h264", "video_decision": "transcode", "stream_video_resolution": "720p"},
    ])
    result = _compute_format_metrics(db_path)
    assert result["quality_profiles"] == ["1080p", "720p", "480p"]


# ── _compute_user_bandwidth ───────────────────────────────────────────────────

def test_compute_user_bandwidth_missing_db_returns_empty_list(tmp_path):
    assert _compute_user_bandwidth(tmp_path / "missing.db") == []


def test_compute_user_bandwidth_no_matching_rows_returns_empty(db_factory):
    db_path = db_factory([{"client_friendly_name": "tv", "stream_video_bitrate": None, "src_video_bitrate": "5000"}])
    assert _compute_user_bandwidth(db_path) == []


def test_compute_user_bandwidth_computes_stats_per_user(db_factory):
    db_path = db_factory([
        {"client_friendly_name": "alice", "stream_video_bitrate": "5000", "src_video_bitrate": "10000"},
        {"client_friendly_name": "alice", "stream_video_bitrate": "10000", "src_video_bitrate": "10000"},
        {"client_friendly_name": "bob", "stream_video_bitrate": "2000", "src_video_bitrate": "10000"},
    ])
    result = _compute_user_bandwidth(db_path)
    by_user = {r["user"]: r for r in result}
    alice = by_user["alice"]
    assert alice["plays"] == 2
    assert alice["avg_pct"] == 75   # (50 + 100) / 2
    assert alice["median_pct"] == 75
    assert alice["min_pct"] == 50
    assert alice["max_pct"] == 100
    assert alice["avg_src"] == 10.0
    assert alice["avg_stream"] == 7.5
    # sorted by avg_pct descending -> alice (75%) before bob (20%)
    assert result[0]["user"] == "alice"


def test_compute_user_bandwidth_handles_missing_user_name(db_factory):
    db_path = db_factory([
        {"client_friendly_name": None, "stream_video_bitrate": "5000", "src_video_bitrate": "10000"},
    ])
    result = _compute_user_bandwidth(db_path)
    assert result[0]["user"] == "Unknown"


def test_compute_user_bandwidth_skips_invalid_bitrates(db_factory):
    db_path = db_factory([
        {"client_friendly_name": "alice", "stream_video_bitrate": "not-a-number", "src_video_bitrate": "10000"},
        {"client_friendly_name": "alice", "stream_video_bitrate": "5000", "src_video_bitrate": "0"},
    ])
    result = _compute_user_bandwidth(db_path)
    assert result == []


# ── _compute_playback_analytics ───────────────────────────────────────────────

def test_compute_playback_analytics_missing_db_returns_none(tmp_path):
    assert _compute_playback_analytics(tmp_path / "missing.db") is None


def test_compute_playback_analytics_empty_table_returns_none(db_factory):
    db_path = db_factory([])
    assert _compute_playback_analytics(db_path) is None


def test_compute_playback_analytics_overall_percentages_and_reasons(db_factory):
    rows = [
        {"transcode_decision": "Direct Play", "client_platform": "Roku", "client_friendly_name": "alice"},
        {"transcode_decision": "Direct Play", "client_platform": "Roku", "client_friendly_name": "alice"},
        {"transcode_decision": "Direct Play", "client_platform": "Roku", "client_friendly_name": "alice"},
        {"transcode_decision": "Transcode", "video_decision": "transcode", "audio_decision": "copy",
         "client_platform": "Chrome", "client_friendly_name": "bob"},
        {"transcode_decision": "Transcode", "video_decision": "copy", "audio_decision": "transcode",
         "client_platform": "Chrome", "client_friendly_name": "bob"},
        {"transcode_decision": "Transcode", "video_decision": "transcode", "audio_decision": "transcode",
         "client_platform": "Chrome", "client_friendly_name": "carl"},
        {"transcode_decision": "Copy", "client_platform": "Chrome", "client_friendly_name": "dana"},
    ]
    result = _compute_playback_analytics(db_factory(rows))

    assert result["total_plays"] == 7
    assert result["direct_pct"] == round(3 / 7 * 100)
    assert result["transcode_pct"] == round(3 / 7 * 100)
    assert result["copy_pct"] == round(1 / 7 * 100)

    reasons = {r["label"]: r["count"] for r in result["transcode_reasons"]}
    assert reasons["Video"] == 1
    assert reasons["Audio"] == 1
    assert reasons["Video + Audio"] == 1

    top = {t["user"]: t["transcode_count"] for t in result["user_transcode_quality"]}
    assert top == {"bob": 2, "carl": 1}


def test_compute_playback_analytics_platform_rates_excludes_low_volume(db_factory):
    rows = [{"transcode_decision": "Direct Play", "client_platform": "Roku", "client_friendly_name": "alice"}] * 2
    rows += [{"transcode_decision": "Transcode", "video_decision": "transcode", "audio_decision": "copy",
              "client_platform": "Chrome", "client_friendly_name": "bob"}] * 4
    result = _compute_playback_analytics(db_factory(rows))
    platforms = {p["platform"] for p in result["platform_rates"]}
    # Roku only has 2 plays (< 3) and is excluded
    assert "Roku" not in platforms
    assert "Chrome" in platforms
    assert result["worst_platform"] == "Chrome"


def test_compute_playback_analytics_audio_codec_and_hdr_outcomes(db_factory):
    rows = [
        {"transcode_decision": "Direct Play", "audio_decision": "direct play", "src_audio_codec": "aac",
         "src_hdr_type": "HDR10", "client_platform": "p", "client_friendly_name": "u1"},
        {"transcode_decision": "Transcode", "video_decision": "transcode", "audio_decision": "transcode",
         "src_audio_codec": "aac", "src_hdr_type": "HDR10",
         "client_platform": "p", "client_friendly_name": "u2"},
    ]
    result = _compute_playback_analytics(db_factory(rows))
    # audio_causes requires >= 2 total plays for that codec
    causes = {a["codec"]: a for a in result["audio_causes"]}
    assert "AAC" in causes
    assert causes["AAC"]["plays"] == 2
    assert causes["AAC"]["transcode_pct"] == 50

    hdr = {h["hdr_type"]: h for h in result["hdr_outcomes"]}
    assert hdr["HDR10"]["plays"] == 2
    assert hdr["HDR10"]["direct_pct"] == 50
    assert hdr["HDR10"]["transcode_pct"] == 50


def test_compute_playback_analytics_no_worst_platform_when_all_direct(db_factory):
    rows = [{"transcode_decision": "Direct Play", "client_platform": "Roku",
             "client_friendly_name": f"u{i}"} for i in range(4)]
    result = _compute_playback_analytics(db_factory(rows))
    assert result["worst_platform"] is None
