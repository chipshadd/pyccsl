# tests/test_idle_time.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pyccsl


def test_imports():
    """Smoke test: pyccsl module is importable."""
    assert hasattr(pyccsl, 'calculate_performance_metrics')
    assert hasattr(pyccsl, 'parse_arguments')
    assert hasattr(pyccsl, 'format_output')


def test_cache_ttl_default(monkeypatch):
    """PYCCSL_CACHE_TTL defaults to 3600 when env var is absent."""
    monkeypatch.delenv("PYCCSL_CACHE_TTL", raising=False)
    monkeypatch.delenv("PYCCSL_FIELDS", raising=False)
    sys.argv = ["pyccsl"]
    config = pyccsl.parse_arguments()
    assert config["cache_ttl"] == 3600


def test_cache_ttl_from_env(monkeypatch):
    """PYCCSL_CACHE_TTL is read from environment variable."""
    monkeypatch.setenv("PYCCSL_CACHE_TTL", "300")
    monkeypatch.delenv("PYCCSL_FIELDS", raising=False)
    sys.argv = ["pyccsl"]
    config = pyccsl.parse_arguments()
    assert config["cache_ttl"] == 300


def _make_entry(entry_type, ts_iso):
    """Helper: build a minimal transcript entry."""
    return {"type": entry_type, "timestamp": ts_iso}


def test_idle_seconds_computed():
    """idle_seconds reflects time since the last assistant entry."""
    # Last assistant message was 90 minutes ago
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    last_ts = now - timedelta(minutes=90)

    entries = [
        _make_entry("user",      (now - timedelta(minutes=100)).isoformat()),
        _make_entry("assistant", last_ts.isoformat()),
        _make_entry("user",      (now - timedelta(minutes=5)).isoformat()),
    ]
    token_totals = {"input_tokens": 0, "output_tokens": 0,
                    "cache_creation_tokens": 0, "cache_read_tokens": 0}

    with patch("pyccsl.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        metrics = pyccsl.calculate_performance_metrics(entries, token_totals)

    assert "idle_seconds" in metrics
    assert abs(metrics["idle_seconds"] - 90 * 60) < 1


def test_idle_seconds_no_assistant_entries():
    """idle_seconds is absent when there are no assistant entries."""
    entries = [_make_entry("user", "2026-04-14T10:00:00+00:00")]
    token_totals = {"input_tokens": 0, "output_tokens": 0,
                    "cache_creation_tokens": 0, "cache_read_tokens": 0}
    metrics = pyccsl.calculate_performance_metrics(entries, token_totals)
    assert "idle_seconds" not in metrics


def test_idle_seconds_clamped_to_zero():
    """idle_seconds is clamped to 0 if clock skew produces a negative value."""
    now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
    future_ts = now + timedelta(seconds=5)  # timestamp slightly in the future

    entries = [_make_entry("assistant", future_ts.isoformat())]
    token_totals = {"input_tokens": 0, "output_tokens": 0,
                    "cache_creation_tokens": 0, "cache_read_tokens": 0}

    with patch("pyccsl.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat.side_effect = datetime.fromisoformat
        metrics = pyccsl.calculate_performance_metrics(entries, token_totals)

    assert metrics["idle_seconds"] == 0.0


def _base_config(fields, cache_ttl=3600, no_emoji=False):
    """Helper: minimal config dict for format_output."""
    return {
        "theme": "none",
        "style": "simple",
        "numbers": "compact",
        "no_emoji": no_emoji,
        "debug": False,
        "cache_thresholds": [95, 90, 75],
        "response_thresholds": [10, 30, 60],
        "cache_ttl": cache_ttl,
        "fields": fields,
    }


def test_idle_time_field_under_ttl():
    """idle-time shows 💤 emoji when under the TTL threshold."""
    metrics = {"idle_seconds": 300.0}  # 5 minutes
    config = _base_config(["idle-time"], cache_ttl=3600)
    result = pyccsl.format_output(config, {}, {}, metrics)
    assert "💤" in result
    assert "⚠️" not in result
    assert "5m" in result


def test_idle_time_field_over_ttl():
    """idle-time shows ⚠️ emoji when over the TTL threshold."""
    metrics = {"idle_seconds": 3700.0}  # just over 1 hour
    config = _base_config(["idle-time"], cache_ttl=3600)
    result = pyccsl.format_output(config, {}, {}, metrics)
    assert "⚠️" in result
    assert "💤" not in result


def test_idle_time_no_emoji_under_ttl():
    """idle-time shows 'Idle:' prefix in no-emoji mode when under threshold."""
    metrics = {"idle_seconds": 120.0}
    config = _base_config(["idle-time"], cache_ttl=3600, no_emoji=True)
    result = pyccsl.format_output(config, {}, {}, metrics)
    assert "Idle:" in result
    assert "CACHE EXPIRED:" not in result


def test_idle_time_no_emoji_over_ttl():
    """idle-time shows 'CACHE EXPIRED:' prefix in no-emoji mode when over threshold."""
    metrics = {"idle_seconds": 7200.0}
    config = _base_config(["idle-time"], cache_ttl=3600, no_emoji=True)
    result = pyccsl.format_output(config, {}, {}, metrics)
    assert "CACHE EXPIRED:" in result


def test_idle_time_absent_when_no_metric():
    """idle-time field produces no output when idle_seconds is not in metrics."""
    metrics = {}
    config = _base_config(["idle-time"])
    result = pyccsl.format_output(config, {}, {}, metrics)
    assert result == ""


def test_idle_time_in_field_order():
    """`idle-time` is present in FIELD_ORDER."""
    assert "idle-time" in pyccsl.FIELD_ORDER
