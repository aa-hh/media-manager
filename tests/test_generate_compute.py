import sqlite3

import pytest

from generate import (
    _compute_format_metrics,
    _compute_user_bandwidth,
    _compute_playback_analytics,
    _compute_buffer_analytics,
)


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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_at INTEGER, session_key TEXT, progress_percent REAL,
                event TEXT, transcode_decision TEXT, video_decision TEXT, audio_decision TEXT,
                subtitle_decision TEXT, quality_profile TEXT, src_video_codec TEXT,
                src_video_resolution TEXT, src_hdr_type TEXT, src_audio_codec TEXT,
                src_audio_channels TEXT, stream_video_codec TEXT, stream_video_resolution TEXT,
                client_platform TEXT, client_friendly_name TEXT, client_device TEXT,
                stream_video_bitrate TEXT, src_video_bitrate TEXT,
                tmdb_id INTEGER, media_type TEXT
            )
        """)
        for row in rows:
            defaults = {
                "event_at": None, "session_key": None, "progress_percent": None,
                "event": "play", "transcode_decision": None, "video_decision": None,
                "audio_decision": None, "subtitle_decision": None, "quality_profile": None,
                "src_video_codec": None, "src_video_resolution": None, "src_hdr_type": None,
                "src_audio_codec": None, "src_audio_channels": None, "stream_video_codec": None,
                "stream_video_resolution": None, "client_platform": None, "client_friendly_name": None,
                "client_device": None,
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


# ── _compute_buffer_analytics ─────────────────────────────────────────────────

def test_compute_buffer_analytics_missing_db_returns_none(tmp_path):
    assert _compute_buffer_analytics(tmp_path / "missing.db") is None


def test_compute_buffer_analytics_empty_table_returns_none(db_factory):
    db_path = db_factory([])
    assert _compute_buffer_analytics(db_path) is None


def test_compute_buffer_analytics_ignores_rows_without_session_key(db_factory):
    rows = [
        {"event": "play", "event_at": 1, "session_key": None, "progress_percent": 5,
         "client_friendly_name": "alice"},
        {"event": "buffer", "event_at": 2, "session_key": None, "client_friendly_name": "alice"},
    ]
    assert _compute_buffer_analytics(db_factory(rows)) is None


def test_compute_buffer_analytics_full_metric_breakdown(db_factory):
    rows = [
        # Session A: buffers twice, then quits (paused at 40%) within 2 minutes of
        # the last buffer -> a buffer-TRIGGERED abandonment. No quality step-down.
        {"event": "play", "event_at": 100, "session_key": "sessA", "progress_percent": 5,
         "stream_video_resolution": "1080p", "client_friendly_name": "alice",
         "client_platform": "Roku", "client_device": "TV"},
        {"event": "buffer", "event_at": 110, "session_key": "sessA",
         "client_friendly_name": "alice", "client_platform": "Roku", "client_device": "TV"},
        {"event": "buffer", "event_at": 200, "session_key": "sessA",
         "client_friendly_name": "alice", "client_platform": "Roku", "client_device": "TV"},
        {"event": "pause", "event_at": 260, "session_key": "sessA", "progress_percent": 40,
         "stream_video_resolution": "1080p", "client_friendly_name": "alice",
         "client_platform": "Roku", "client_device": "TV"},

        # Session B: buffers once early, plays on for ~16 minutes, then quits at 50%.
        # Abandoned, but the buffer is too far removed (990s gap) to call it triggered.
        {"event": "play", "event_at": 1000, "session_key": "sessB", "progress_percent": 5,
         "stream_video_resolution": "1080p", "client_friendly_name": "bob",
         "client_platform": "Chrome", "client_device": "Laptop"},
        {"event": "buffer", "event_at": 1010, "session_key": "sessB",
         "client_friendly_name": "bob", "client_platform": "Chrome", "client_device": "Laptop"},
        {"event": "stop", "event_at": 2000, "session_key": "sessB", "progress_percent": 50,
         "stream_video_resolution": "1080p", "client_friendly_name": "bob",
         "client_platform": "Chrome", "client_device": "Laptop"},

        # Session C: buffers, the stream steps down to a lower resolution afterward,
        # but the viewer finishes anyway -> completed, with a behavior change.
        {"event": "play", "event_at": 3000, "session_key": "sessC", "progress_percent": 2,
         "stream_video_resolution": "1080p", "client_friendly_name": "carl",
         "client_platform": "Apple TV", "client_device": "ATV"},
        {"event": "buffer", "event_at": 3010, "session_key": "sessC",
         "client_friendly_name": "carl", "client_platform": "Apple TV", "client_device": "ATV"},
        {"event": "resume", "event_at": 3020, "session_key": "sessC", "progress_percent": 50,
         "stream_video_resolution": "480p", "client_friendly_name": "carl",
         "client_platform": "Apple TV", "client_device": "ATV"},
        {"event": "stop", "event_at": 3500, "session_key": "sessC", "progress_percent": 97,
         "stream_video_resolution": "480p", "client_friendly_name": "carl",
         "client_platform": "Apple TV", "client_device": "ATV"},

        # Session D: buffers once, finishes at the same resolution -> no behavior change.
        {"event": "play", "event_at": 4000, "session_key": "sessD", "progress_percent": 1,
         "stream_video_resolution": "720p", "client_friendly_name": "dana",
         "client_platform": "Android", "client_device": "Phone"},
        {"event": "buffer", "event_at": 4010, "session_key": "sessD",
         "client_friendly_name": "dana", "client_platform": "Android", "client_device": "Phone"},
        {"event": "resume", "event_at": 4020, "session_key": "sessD", "progress_percent": 60,
         "stream_video_resolution": "720p", "client_friendly_name": "dana",
         "client_platform": "Android", "client_device": "Phone"},
        {"event": "stop", "event_at": 4500, "session_key": "sessD", "progress_percent": 98,
         "stream_video_resolution": "720p", "client_friendly_name": "dana",
         "client_platform": "Android", "client_device": "Phone"},

        # Session E: never buffers, finishes -> only affects the general baseline.
        {"event": "play", "event_at": 5000, "session_key": "sessE", "progress_percent": 1,
         "stream_video_resolution": "1080p", "client_friendly_name": "erin",
         "client_platform": "Web", "client_device": "Browser"},
        {"event": "stop", "event_at": 5100, "session_key": "sessE", "progress_percent": 99,
         "stream_video_resolution": "1080p", "client_friendly_name": "erin",
         "client_platform": "Web", "client_device": "Browser"},
    ]
    result = _compute_buffer_analytics(db_factory(rows))

    # General baseline: 2 of 5 sessions ended early (A and B), regardless of buffering.
    assert result["general_abandon_rate_pct"] == round(2 / 5 * 100)

    # 4 of the 5 sessions buffered (A, B, C, D); only A's quit followed its last
    # buffer within the 120s window -> 1 buffer-triggered abandonment.
    assert result["buffered_sessions"] == 4
    assert result["buffer_triggered_abandon_rate_pct"] == round(1 / 4 * 100)
    assert result["avg_buffers_until_triggered_abandon"] == 2.0

    # 5 buffer incidents evaluated total (2 in A, 1 each in B/C/D); only C's was
    # followed by a step-down (1080p -> 480p) -> 1/5.
    assert result["quality_drop_after_buffer_pct"] == round(1 / 5 * 100)

    # Of the 4 buffered sessions, only D finished without quitting or changing quality.
    assert result["no_behavior_change_pct"] == round(1 / 4 * 100)

    by_user = {row["user"]: row["buffer_count"] for row in result["buffer_by_user"]}
    assert by_user == {"alice": 2, "bob": 1, "carl": 1, "dana": 1}

    by_client = {(row["user"], row["client"]): row["buffer_count"] for row in result["buffer_by_client"]}
    assert by_client == {
        ("alice", "Roku / TV"): 2,
        ("bob", "Chrome / Laptop"): 1,
        ("carl", "Apple TV / ATV"): 1,
        ("dana", "Android / Phone"): 1,
    }


def test_compute_buffer_analytics_collapses_rapid_repeated_buffer_firings(db_factory):
    rows = [
        {"event": "play", "event_at": 1000, "session_key": "sessX", "progress_percent": 5,
         "stream_video_resolution": "1080p", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
        # Tautulli re-fires the Buffer Warning every ~9s for one sustained incident —
        # these three should collapse into a single logical buffering event.
        {"event": "buffer", "event_at": 1010, "session_key": "sessX", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
        {"event": "buffer", "event_at": 1019, "session_key": "sessX", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
        {"event": "buffer", "event_at": 1028, "session_key": "sessX", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
        # A genuinely separate incident later (gap > 15s) counts on its own.
        {"event": "buffer", "event_at": 1100, "session_key": "sessX", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
        {"event": "stop", "event_at": 1200, "session_key": "sessX", "progress_percent": 96,
         "stream_video_resolution": "1080p", "client_friendly_name": "fay",
         "client_platform": "Fire TV", "client_device": "Stick"},
    ]
    result = _compute_buffer_analytics(db_factory(rows))

    # 4 raw buffer rows -> 2 logical incidents (one cluster of 3 + one standalone)
    assert result["buffered_sessions"] == 1
    by_user = {row["user"]: row["buffer_count"] for row in result["buffer_by_user"]}
    assert by_user == {"fay": 2}

    by_client = {(row["user"], row["client"]): row["buffer_count"] for row in result["buffer_by_client"]}
    assert by_client == {("fay", "Fire TV / Stick"): 2}


def test_compute_buffer_analytics_session_resumed_before_threshold_is_not_abandoned(db_factory):
    rows = [
        {"event": "play", "event_at": 1, "session_key": "sess1", "progress_percent": 5,
         "stream_video_resolution": "1080p", "client_friendly_name": "dana"},
        {"event": "buffer", "event_at": 2, "session_key": "sess1", "client_friendly_name": "dana"},
        {"event": "pause", "event_at": 3, "session_key": "sess1", "progress_percent": 30,
         "stream_video_resolution": "1080p", "client_friendly_name": "dana"},
        # Resumes after the pause, so the session should NOT be counted as abandoned
        # even though the last pause was well below the threshold.
        {"event": "resume", "event_at": 100, "session_key": "sess1", "progress_percent": 31,
         "stream_video_resolution": "1080p", "client_friendly_name": "dana"},
        {"event": "stop", "event_at": 200, "session_key": "sess1", "progress_percent": 97,
         "stream_video_resolution": "1080p", "client_friendly_name": "dana"},
    ]
    result = _compute_buffer_analytics(db_factory(rows))
    assert result["general_abandon_rate_pct"] == 0
    assert result["buffer_triggered_abandon_rate_pct"] == 0
    assert result["no_behavior_change_pct"] == 100


def test_compute_buffer_analytics_pause_near_end_is_not_abandoned(db_factory):
    rows = [
        {"event": "play", "event_at": 1, "session_key": "sess1", "progress_percent": 5,
         "stream_video_resolution": "1080p", "client_friendly_name": "erin"},
        # Ends on a pause, but at 95% — treated as essentially finished, not abandoned.
        {"event": "pause", "event_at": 2, "session_key": "sess1", "progress_percent": 95,
         "stream_video_resolution": "1080p", "client_friendly_name": "erin"},
    ]
    result = _compute_buffer_analytics(db_factory(rows))
    assert result["general_abandon_rate_pct"] == 0
