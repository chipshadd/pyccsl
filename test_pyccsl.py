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


class CacheStateTests(unittest.TestCase):
    NOW = 1_789_272_000

    def pc(self, seconds_left, warm=True):
        return {"warm": warm, "expires_at": self.NOW + seconds_left, "recache_tokens_if_cold": 161136}

    def test_hidden_without_prompt_cache(self):
        self.assertIsNone(pyccsl.cache_state(None, self.NOW))

    def test_hidden_when_warm_without_expiry(self):
        self.assertIsNone(pyccsl.cache_state({"warm": True, "expires_at": None}, self.NOW))

    def test_cold_when_not_warm(self):
        self.assertEqual(pyccsl.cache_state({"warm": False, "expires_at": None}, self.NOW), ("cold", None))

    def test_cold_when_expired(self):
        self.assertEqual(pyccsl.cache_state(self.pc(0), self.NOW), ("cold", None))
        self.assertEqual(pyccsl.cache_state(self.pc(-30), self.NOW), ("cold", None))

    def test_levels_follow_displayed_minutes(self):
        cases = [
            (16 * 60, ("warm", 16)),
            (15 * 60 + 59, ("low", 15)),
            (6 * 60, ("low", 6)),
            (5 * 60 + 59, ("critical", 5)),
            (59, ("critical", 0)),
        ]
        for seconds_left, expected in cases:
            with self.subTest(seconds_left=seconds_left):
                self.assertEqual(pyccsl.cache_state(self.pc(seconds_left), self.NOW), expected)


class CacheTextTests(unittest.TestCase):
    def test_warm_text(self):
        self.assertEqual(pyccsl.format_cache_text("warm", 48, 161136, False), "⏳ 48m left")

    def test_last_minute(self):
        self.assertEqual(pyccsl.format_cache_text("critical", 0, 161136, False), "⏳ <1m left")

    def test_cold_text(self):
        self.assertEqual(pyccsl.format_cache_text("cold", None, 161136, False), "🧊 cold · 161.1K re-ingest")
        self.assertEqual(pyccsl.format_cache_text("cold", None, None, False), "🧊 cold")

    def test_no_emoji(self):
        self.assertEqual(pyccsl.format_cache_text("warm", 48, 161136, True), "Cache: 48m left")
        self.assertEqual(pyccsl.format_cache_text("cold", None, 161136, True), "Cache: cold (161.1K)")
        self.assertEqual(pyccsl.format_cache_text("cold", None, None, True), "Cache: cold")


class CacheColorTests(unittest.TestCase):
    def test_colors(self):
        theme = pyccsl.THEMES["default"]
        self.assertEqual(pyccsl.cache_color("warm", theme), theme["input"])
        self.assertEqual(pyccsl.cache_color("low", theme), 220)
        self.assertEqual(pyccsl.cache_color("critical", theme), 196)
        self.assertEqual(pyccsl.cache_color("cold", theme), 196)
        self.assertIsNone(pyccsl.cache_color("cold", pyccsl.THEMES["none"]))


class CacheFieldRenderTests(unittest.TestCase):
    def test_powerline_segment_uses_level_background(self):
        config = render_config(fields=["cache"])
        metrics = {"cache_state": ("low", 12), "cache_recache_tokens": 161136}
        out = pyccsl.format_output(config, {"display_name": "Opus 5"}, {"cwd": "/tmp"}, metrics)
        self.assertIn("\x1b[38;5;0;48;5;220m ⏳ 12m left ", out)

    def test_collect_reads_prompt_cache(self):
        now = 1_789_272_000
        data = {"prompt_cache": {"warm": True, "expires_at": now + 48 * 60 + 30,
                                 "recache_tokens_if_cold": 161136}}
        metrics = pyccsl.collect_cache_and_usage(data, now)
        self.assertEqual(metrics["cache_state"], ("warm", 48))
        self.assertEqual(metrics["cache_recache_tokens"], 161136)


class CacheFieldWiringTests(unittest.TestCase):
    def test_hidden_when_warm_missing(self):
        self.assertIsNone(pyccsl.cache_state({"expires_at": 1_789_272_600}, 1_789_272_000))

    def test_hidden_until_first_response(self):
        self.assertNotIn("cache_state", pyccsl.collect_cache_and_usage({}, 1_789_272_000))
        config = render_config(fields=["model", "cache"])
        out = pyccsl.format_output(config, {"display_name": "Opus 5"}, {"cwd": "/tmp"}, {})
        self.assertEqual(strip_ansi(out), " Opus 5 " + pyccsl.POWERLINE_RIGHT)

    def test_cold_no_emoji_segment(self):
        config = render_config(fields=["cache"], no_emoji=True)
        metrics = {"cache_state": ("cold", None), "cache_recache_tokens": 161136}
        out = pyccsl.format_output(config, {}, {"cwd": "/tmp"}, metrics)
        self.assertIn("\x1b[38;5;0;48;5;196m Cache: cold (161.1K) ", out)

    def test_pipes_colors_cache_foreground_and_not_badge(self):
        config = render_config(style="pipes", fields=["badge", "cache"], no_emoji=True)
        metrics = {"badge": "B", "cache_state": ("critical", 3)}
        out = pyccsl.format_output(config, {}, {"cwd": "/tmp"}, metrics)
        self.assertEqual(out, "B | \x1b[38;5;196mCache: 3m left\x1b[0m")

    def test_cache_follows_tokens(self):
        self.assertEqual(pyccsl.FIELD_ORDER.index("cache"), pyccsl.FIELD_ORDER.index("tokens") + 1)


if __name__ == "__main__":
    unittest.main()
