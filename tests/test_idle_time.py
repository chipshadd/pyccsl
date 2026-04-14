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
