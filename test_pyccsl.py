import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

import pyccsl

PYCCSL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyccsl.py")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return ANSI.sub("", text)


def render_config(style="powerline", theme="default", fields=None, no_emoji=False):
    return {
        "theme": theme,
        "numbers": "full",
        "style": style,
        "no_emoji": no_emoji,
        "debug": False,
        "cache_thresholds": [95, 90, 75],
        "response_thresholds": [10, 30, 60],
        "fields": fields or [],
    }


class GaugeTests(unittest.TestCase):
    def test_reference_values(self):
        cases = {
            0: "░░░░░", 1: "▏░░░░", 7: "▍░░░░", 7.000000000000001: "▍░░░░",
            11: "▌░░░░", 51: "██▌░░", 78: "███▉░", 92: "████▋",
            100: "█████", 120: "█████",
        }
        for percent, expected in cases.items():
            with self.subTest(percent=percent):
                self.assertEqual(pyccsl.render_gauge(percent), expected)

    def test_value_that_rounds_to_zero_stays_empty(self):
        self.assertEqual(pyccsl.render_gauge(0.4), "░░░░░")


class UsageLevelTests(unittest.TestCase):
    def test_boundaries_use_rounded_percent(self):
        cases = {74: 0, 74.6: 1, 75: 1, 89: 1, 90: 2, 100: 2}
        for percent, expected in cases.items():
            with self.subTest(percent=percent):
                self.assertEqual(pyccsl.usage_level(percent), expected)


class UsagePercentagesTests(unittest.TestCase):
    def test_reads_both_windows(self):
        data = {"rate_limits": {
            "five_hour": {"used_percentage": 7.000000000000001, "resets_at": 1},
            "seven_day": {"used_percentage": 51, "resets_at": 2},
        }}
        self.assertEqual(pyccsl.usage_percentages(data),
                         {"usage-5h": 7.000000000000001, "usage-week": 51})

    def test_missing_rate_limits_gives_nothing(self):
        self.assertEqual(pyccsl.usage_percentages({}), {})
        self.assertEqual(pyccsl.usage_percentages({"rate_limits": None}), {})

    def test_missing_or_null_window_is_skipped(self):
        data = {"rate_limits": {"five_hour": {"used_percentage": None},
                                "seven_day": {"used_percentage": 51}}}
        self.assertEqual(pyccsl.usage_percentages(data), {"usage-week": 51})


class RenderUsageTests(unittest.TestCase):
    def test_plain_mode(self):
        self.assertEqual(pyccsl.render_usage("5h", 7, "none"), "5h ▍░░░░ 7%")

    def test_fg_mode_strips_to_plain(self):
        text = pyccsl.render_usage("wk", 51, "fg")
        self.assertIn("\x1b[38;5;82m", text)
        self.assertEqual(strip_ansi(text), "wk ██▌░░ 51%")

    def test_panel_mode_never_resets(self):
        text = pyccsl.render_usage("5h", 92, "panel")
        self.assertNotIn("\x1b[0m", text)
        self.assertIn("\x1b[38;5;196m", text)
        self.assertTrue(text.endswith("\x1b[38;5;250m"))
        self.assertEqual(strip_ansi(text), "5h ████▋ 92%")


class UsagePanelTests(unittest.TestCase):
    def render(self, **kwargs):
        config = render_config(fields=["usage-5h", "usage-week"], **kwargs)
        metrics = {"usage": {"usage-5h": 7, "usage-week": 51}}
        return pyccsl.format_output(config, {"display_name": "Opus 5"}, {"cwd": "/tmp"}, metrics)

    def test_powerline_panel_is_one_segment(self):
        out = self.render()
        start = out.index("\x1b[38;5;250;48;5;236m")
        end = out.index("\x1b[0m", start)
        panel = out[start:end]
        self.assertIn("5h", panel)
        self.assertIn("51%", panel)
        self.assertIn(pyccsl.POWERLINE_THIN, panel)

    def test_theme_none_has_no_escape_codes(self):
        out = self.render(theme="none", style="pipes")
        self.assertEqual(out, "5h ▍░░░░ 7% | wk ██▌░░ 51%")


class ExistingPowerlineTests(unittest.TestCase):
    def test_existing_segments_keep_black_text_and_plain_joiner(self):
        config = render_config(fields=["model", "output", "tokens", "usage-5h"])
        metrics = {"output_tokens": 4321, "context_size": 161136, "usage": {"usage-5h": 7}}
        out = pyccsl.format_output(config, {"display_name": "Opus 5"}, {"cwd": "/tmp"}, metrics)
        start = out.index("\x1b[38;5;0;48;5;214m")
        group = out[start:out.index("\x1b[0m", start)]
        self.assertEqual(strip_ansi(group), " ↓ 4,321 ⧉ 161,136 ")
        self.assertNotIn(pyccsl.POWERLINE_THIN, group)


class UsageModeTests(unittest.TestCase):
    def render(self, style, theme):
        config = render_config(style=style, theme=theme, fields=["usage-5h", "usage-week"])
        return pyccsl.format_output(config, {}, {"cwd": "/tmp"}, {"usage": {"usage-5h": 7, "usage-week": 51}})

    def test_themed_non_powerline_colors_the_gauge(self):
        out = self.render("pipes", "default")
        self.assertIn("\x1b[38;5;82m", out)
        self.assertEqual(strip_ansi(out), "5h ▍░░░░ 7% | wk ██▌░░ 51%")

    def test_powerline_theme_none_has_no_escape_codes(self):
        self.assertNotIn("\x1b", self.render("powerline", "none"))


if __name__ == "__main__":
    unittest.main()
