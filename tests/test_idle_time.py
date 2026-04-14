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
