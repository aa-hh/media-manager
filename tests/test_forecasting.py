import json
from datetime import date, timedelta

from lib.processors import forecasting


def _write_snapshots(data_dir, snapshots):
    (data_dir / "snapshots.json").write_text(json.dumps(snapshots))


# ── calculate ─────────────────────────────────────────────────────────────────

def test_calculate_missing_file_returns_empty_result(tmp_path):
    result = forecasting.calculate(tmp_path)
    assert result == {"snapshots": [], "growth_gb_per_month": None, "predicted_full_date": None}


def test_calculate_corrupt_file_returns_empty_result(tmp_path):
    (tmp_path / "snapshots.json").write_text("not json")
    result = forecasting.calculate(tmp_path)
    assert result["growth_gb_per_month"] is None


def test_calculate_single_snapshot_returns_no_growth(tmp_path):
    _write_snapshots(tmp_path, [{"date": "2024-01-01", "tv_gb": 1, "movie_gb": 1, "total_gb": 2}])
    result = forecasting.calculate(tmp_path)
    assert result["growth_gb_per_month"] is None
    assert result["predicted_full_date"] is None
    assert len(result["snapshots"]) == 1


def test_calculate_growth_rate_from_two_snapshots(tmp_path):
    snaps = [
        {"date": "2024-01-01", "tv_gb": 50, "movie_gb": 50, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 60, "movie_gb": 60, "total_gb": 120},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path)
    # 20 GB over 10 days = 2 GB/day => 2 * 30.44 = 60.88 GB/month
    assert result["growth_gb_per_month"] == 60.88
    assert result["current_total_gb"] == 120
    assert result["predicted_full_date"] is None  # no capacity given


def test_calculate_predicts_full_date_when_capacity_given(tmp_path):
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 0, "movie_gb": 0, "total_gb": 110},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path, capacity_gb=200)
    # daily_growth = 1 GB/day, remaining = 90 -> 90 days to full
    expected_date = (date.today() + timedelta(days=90)).isoformat()
    assert result["predicted_full_date"] == expected_date
    assert result["capacity_gb"] == 200


def test_calculate_no_prediction_when_growth_not_positive(tmp_path):
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path, capacity_gb=200)
    assert result["growth_gb_per_month"] is None
    assert result["predicted_full_date"] is None


def test_calculate_uses_only_last_30_snapshots(tmp_path):
    base_date = date(2024, 1, 1)
    snaps = [
        {"date": (base_date + timedelta(days=i)).isoformat(), "tv_gb": 0, "movie_gb": 0, "total_gb": i}
        for i in range(40)
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path)
    # last 30 entries span values 10..39 over 29 days -> daily growth = 1.0
    assert result["growth_gb_per_month"] == round(1.0 * 30.44, 2)
    assert result["current_total_gb"] == 39


# ── record_snapshot ───────────────────────────────────────────────────────────

def test_record_snapshot_creates_file_when_missing(tmp_path):
    forecasting.record_snapshot(tmp_path, [{"size_gb": 10}], [{"size_gb": 5}])
    data = json.loads((tmp_path / "snapshots.json").read_text())
    assert len(data) == 1
    assert data[0]["tv_gb"] == 10
    assert data[0]["movie_gb"] == 5
    assert data[0]["total_gb"] == 15
    assert data[0]["date"] == date.today().isoformat()


def test_record_snapshot_replaces_todays_entry(tmp_path):
    today = date.today().isoformat()
    _write_snapshots(tmp_path, [{"date": today, "tv_gb": 1, "movie_gb": 1, "total_gb": 2}])
    forecasting.record_snapshot(tmp_path, [{"size_gb": 100}], [{"size_gb": 50}])
    data = json.loads((tmp_path / "snapshots.json").read_text())
    assert len(data) == 1
    assert data[0]["total_gb"] == 150


def test_record_snapshot_appends_and_sorts(tmp_path):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _write_snapshots(tmp_path, [{"date": yesterday, "tv_gb": 1, "movie_gb": 1, "total_gb": 2}])
    forecasting.record_snapshot(tmp_path, [{"size_gb": 10}], [{"size_gb": 10}])
    data = json.loads((tmp_path / "snapshots.json").read_text())
    assert len(data) == 2
    assert [s["date"] for s in data] == sorted(s["date"] for s in data)


def test_record_snapshot_truncates_to_730_days(tmp_path):
    base_date = date(2020, 1, 1)
    existing = [
        {"date": (base_date + timedelta(days=i)).isoformat(), "tv_gb": 1, "movie_gb": 1, "total_gb": 2}
        for i in range(800)
    ]
    _write_snapshots(tmp_path, existing)
    forecasting.record_snapshot(tmp_path, [{"size_gb": 1}], [{"size_gb": 1}])
    data = json.loads((tmp_path / "snapshots.json").read_text())
    assert len(data) == 730


def test_record_snapshot_handles_corrupt_existing_file(tmp_path):
    (tmp_path / "snapshots.json").write_text("{not valid json")
    forecasting.record_snapshot(tmp_path, [{"size_gb": 1}], [{"size_gb": 1}])
    data = json.loads((tmp_path / "snapshots.json").read_text())
    assert len(data) == 1


def test_calculate_growth_none_when_days_span_lt_7(tmp_path):
    """Snapshots only 3 days apart → insufficient window → None even with real growth."""
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-04", "tv_gb": 0, "movie_gb": 0, "total_gb": 130},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path)
    assert result["growth_gb_per_month"] is None


def test_calculate_growth_none_when_monthly_growth_is_zero(tmp_path):
    """Identical total_gb across snapshots → zero growth → None."""
    snaps = [
        {"date": "2024-01-01", "tv_gb": 50, "movie_gb": 50, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 50, "movie_gb": 50, "total_gb": 100},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path, capacity_gb=500)
    assert result["growth_gb_per_month"] is None


def test_calculate_days_until_full_populated(tmp_path):
    """Real growth + capacity → days_until_full is a positive integer."""
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 0, "movie_gb": 0, "total_gb": 110},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path, capacity_gb=200)
    assert result["days_until_full"] is not None
    assert isinstance(result["days_until_full"], int)
    assert result["days_until_full"] > 0


def test_calculate_days_until_full_none_when_no_capacity(tmp_path):
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 0, "movie_gb": 0, "total_gb": 120},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path)
    assert result["days_until_full"] is None


def test_calculate_days_until_full_none_when_no_growth(tmp_path):
    snaps = [
        {"date": "2024-01-01", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
        {"date": "2024-01-11", "tv_gb": 0, "movie_gb": 0, "total_gb": 100},
    ]
    _write_snapshots(tmp_path, snaps)
    result = forecasting.calculate(tmp_path, capacity_gb=500)
    assert result["days_until_full"] is None
