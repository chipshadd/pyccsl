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
        metrics = pyccsl.collect_cache_and_usage(data, ["cache"], now)
        self.assertEqual(metrics["cache_state"], ("warm", 48))
        self.assertEqual(metrics["cache_recache_tokens"], 161136)


class CacheFieldWiringTests(unittest.TestCase):
    def test_hidden_when_warm_missing(self):
        self.assertIsNone(pyccsl.cache_state({"expires_at": 1_789_272_600}, 1_789_272_000))

    def test_hidden_until_first_response(self):
        self.assertNotIn("cache_state", pyccsl.collect_cache_and_usage({}, ["cache"], 1_789_272_000))
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


FABLE_RESPONSE = {
    "five_hour": {"utilization": 7.0, "resets_at": "2026-09-13T05:00:00.471846+00:00"},
    "seven_day": {"utilization": 51.0, "resets_at": "2026-09-16T15:00:00.471899+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 7,
         "resets_at": "2026-09-13T05:00:00.471846+00:00", "scope": None},
        {"kind": "weekly_all", "group": "weekly", "percent": 51,
         "resets_at": "2026-09-16T15:00:00.471899+00:00", "scope": None},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 11,
         "resets_at": "2026-09-16T15:00:00.472267+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
    ],
}


class ExtractFableTests(unittest.TestCase):
    def test_real_response(self):
        self.assertEqual(pyccsl.extract_fable(FABLE_RESPONSE),
                         {"percent": 11, "resets_at": "2026-09-16T15:00:00.472267+00:00"})

    def test_name_is_case_insensitive(self):
        payload = json.loads(json.dumps(FABLE_RESPONSE))
        payload["limits"][2]["scope"]["model"]["display_name"] = "FABLE"
        self.assertEqual(pyccsl.extract_fable(payload)["percent"], 11)

    def test_no_fable_entry(self):
        self.assertIsNone(pyccsl.extract_fable({"limits": FABLE_RESPONSE["limits"][:2]}))

    def test_malformed(self):
        for payload in (None, [], {"limits": None},
                        {"limits": ["x", {"kind": "weekly_scoped", "scope": "x"}]}):
            with self.subTest(payload=payload):
                self.assertIsNone(pyccsl.extract_fable(payload))

    def test_nan_percent_is_treated_as_no_entry(self):
        payload = json.loads(json.dumps(FABLE_RESPONSE))
        payload["limits"][2]["percent"] = float("nan")
        self.assertIsNone(pyccsl.extract_fable(payload))

    def test_extra_entry_before_weekly_is_skipped(self):
        payload = json.loads(json.dumps(FABLE_RESPONSE))
        payload["limits"].insert(0, {"kind": "session_scoped", "percent": 99,
                                     "scope": {"model": {"display_name": "Fable"}}})
        self.assertEqual(pyccsl.extract_fable(payload)["percent"], 11)


class UsageCacheTests(unittest.TestCase):
    NOW = 1_789_272_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "pyccsl", "usage.json")
        self.spawned = []

    def spawn(self):
        self.spawned.append(True)

    def test_fresh_value_is_shown(self):
        cache = {"fetched_at": self.NOW - 60, "fable": {"percent": 11}}
        self.assertEqual(pyccsl.fable_from_cache(cache, self.NOW), 11)

    def test_stale_or_missing_value_is_hidden(self):
        stale = {"fetched_at": self.NOW - 1801, "fable": {"percent": 11}}
        self.assertIsNone(pyccsl.fable_from_cache(stale, self.NOW))
        self.assertIsNone(pyccsl.fable_from_cache({}, self.NOW))
        self.assertIsNone(pyccsl.fable_from_cache({"fetched_at": self.NOW, "fable": None}, self.NOW))

    def test_exact_stale_boundary_is_still_shown(self):
        cache = {"fetched_at": self.NOW - 1800, "fable": {"percent": 11}}
        self.assertEqual(pyccsl.fable_from_cache(cache, self.NOW), 11)

    def test_non_finite_or_bool_percent_is_hidden(self):
        for percent in (float("nan"), float("inf"), True):
            with self.subTest(percent=percent):
                cache = {"fetched_at": self.NOW, "fable": {"percent": percent}}
                self.assertIsNone(pyccsl.fable_from_cache(cache, self.NOW))

    def test_future_fetched_at_is_hidden(self):
        cache = {"fetched_at": self.NOW + 60, "fable": {"percent": 11}}
        self.assertIsNone(pyccsl.fable_from_cache(cache, self.NOW))

    def test_future_attempted_at_spawns(self):
        cache = {"attempted_at": self.NOW + 60}
        self.assertTrue(pyccsl.maybe_start_fetch(self.path, cache, self.NOW, self.spawn))
        self.assertEqual(self.spawned, [True])

    def test_recent_attempt_starts_nothing(self):
        cache = {"attempted_at": self.NOW - 300}
        self.assertFalse(pyccsl.maybe_start_fetch(self.path, cache, self.NOW, self.spawn))
        self.assertEqual(self.spawned, [])
        self.assertFalse(os.path.exists(self.path))

    def test_stale_attempt_stamps_then_spawns(self):
        cache = {"attempted_at": self.NOW - 301, "fetched_at": 5, "fable": {"percent": 11}}
        self.assertTrue(pyccsl.maybe_start_fetch(self.path, cache, self.NOW, self.spawn))
        self.assertEqual(self.spawned, [True])
        self.assertEqual(pyccsl.read_json_file(self.path),
                         {"attempted_at": self.NOW, "fetched_at": 5, "fable": {"percent": 11}})

    def test_missing_file_spawns(self):
        self.assertTrue(pyccsl.maybe_start_fetch(self.path, {}, self.NOW, self.spawn))
        self.assertEqual(self.spawned, [True])

    def test_failed_stamp_spawns_nothing(self):
        blocker = os.path.join(self.tmp.name, "blocker")
        open(blocker, "w").close()
        path = os.path.join(blocker, "usage.json")
        self.assertFalse(pyccsl.maybe_start_fetch(path, {}, self.NOW, self.spawn))
        self.assertEqual(self.spawned, [])

    def test_read_json_file_tolerates_garbage(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(pyccsl.read_json_file(self.path), {})

    def test_write_json_atomic_failure_leaves_file_and_dir_untouched(self):
        pyccsl.write_json_atomic(self.path, {"old": 1})
        self.assertFalse(pyccsl.write_json_atomic(self.path, {"bad": object()}))
        self.assertEqual(pyccsl.read_json_file(self.path), {"old": 1})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["usage.json"])


class SpawnFetchTests(unittest.TestCase):
    def test_child_is_fully_detached(self):
        with mock.patch.object(pyccsl.subprocess, "Popen") as popen:
            pyccsl.spawn_fetch()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:], [os.path.abspath(pyccsl.__file__), "--fetch-usage"])
        self.assertTrue(kwargs["start_new_session"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertIs(kwargs[stream], subprocess.DEVNULL)


class NoRedirectTests(unittest.TestCase):
    def test_redirect_is_not_followed_and_token_not_forwarded(self):
        import http.server
        import threading

        b_hits = []

        class HandlerB(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                b_hits.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = b"{}"
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        def stop(server, thread):
            server.shutdown()
            thread.join()
            server.server_close()

        server_b = http.server.HTTPServer(("127.0.0.1", 0), HandlerB)
        thread_b = threading.Thread(target=server_b.serve_forever, daemon=True)
        thread_b.start()
        self.addCleanup(stop, server_b, thread_b)
        b_url = f"http://127.0.0.1:{server_b.server_port}/"

        class HandlerA(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", b_url)
                self.end_headers()

            def log_message(self, *args):
                pass

        server_a = http.server.HTTPServer(("127.0.0.1", 0), HandlerA)
        thread_a = threading.Thread(target=server_a.serve_forever, daemon=True)
        thread_a.start()
        self.addCleanup(stop, server_a, thread_a)
        a_url = f"http://127.0.0.1:{server_a.server_port}/"

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            pyccsl.http_get_json(a_url, {"Authorization": "Bearer t"}, 5)
        self.assertEqual(ctx.exception.code, 302)
        self.assertEqual(b_hits, [])


class FetchUsageTests(unittest.TestCase):
    NOW = 1_789_272_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache_path = os.path.join(self.tmp.name, "cache", "usage.json")
        self.creds_path = os.path.join(self.tmp.name, ".credentials.json")
        self.calls = []

    def write_creds(self, expires_in_seconds):
        with open(self.creds_path, "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": "tok", "refreshToken": "never-used",
                                         "expiresAt": (self.NOW + expires_in_seconds) * 1000}}, f)

    def seed_cache(self):
        pyccsl.write_json_atomic(self.cache_path, {
            "attempted_at": self.NOW, "fetched_at": self.NOW - 600,
            "fable": {"percent": 9, "resets_at": "old"}})

    def fake_get(self, response=None, error=None):
        def get(url, headers, timeout):
            self.calls.append((url, headers, timeout))
            if error:
                raise error
            return response
        return get

    def test_success_records_fable(self):
        self.write_creds(3600)
        self.seed_cache()
        pyccsl.run_fetch_usage(self.cache_path, self.creds_path, self.NOW, self.fake_get(FABLE_RESPONSE))
        url, headers, timeout = self.calls[0]
        self.assertEqual(url, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(headers["anthropic-beta"], "oauth-2025-04-20")
        self.assertEqual(timeout, 5)
        self.assertEqual(pyccsl.read_json_file(self.cache_path), {
            "attempted_at": self.NOW, "fetched_at": self.NOW,
            "fable": {"percent": 11, "resets_at": "2026-09-16T15:00:00.472267+00:00"}})

    def test_expired_token_sends_nothing(self):
        self.write_creds(-1)
        pyccsl.run_fetch_usage(self.cache_path, self.creds_path, self.NOW, self.fake_get(FABLE_RESPONSE))
        self.assertEqual(self.calls, [])

    def test_missing_credentials_send_nothing(self):
        pyccsl.run_fetch_usage(self.cache_path, self.creds_path, self.NOW, self.fake_get(FABLE_RESPONSE))
        self.assertEqual(self.calls, [])

    def test_http_error_keeps_last_good_value(self):
        self.write_creds(3600)
        self.seed_cache()
        error = urllib.error.HTTPError("https://api.anthropic.com/api/oauth/usage", 401, "Unauthorized", {}, None)
        pyccsl.run_fetch_usage(self.cache_path, self.creds_path, self.NOW, self.fake_get(error=error))
        cache = pyccsl.read_json_file(self.cache_path)
        self.assertEqual((cache["fetched_at"], cache["fable"]), (self.NOW - 600, {"percent": 9, "resets_at": "old"}))

    def test_response_without_fable_keeps_last_good_value(self):
        self.write_creds(3600)
        self.seed_cache()
        pyccsl.run_fetch_usage(self.cache_path, self.creds_path, self.NOW, self.fake_get({"limits": []}))
        self.assertEqual(pyccsl.read_json_file(self.cache_path)["fetched_at"], self.NOW - 600)

    def test_fetch_usage_mode_exits_cleanly_without_credentials(self):
        env = dict(os.environ, XDG_CACHE_HOME=self.tmp.name, CLAUDE_CONFIG_DIR=self.tmp.name)
        result = subprocess.run([sys.executable, PYCCSL, "--fetch-usage"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class CollectFableTests(unittest.TestCase):
    NOW = 1_789_272_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spawned = []

    def spawn(self):
        self.spawned.append(True)

    def test_no_fetch_without_the_field(self):
        metrics = pyccsl.collect_cache_and_usage({}, ["cache", "usage-5h"], self.NOW, self.spawn)
        self.assertEqual(self.spawned, [])
        self.assertNotIn("usage-fable", metrics["usage"])

    def test_fresh_cache_shows_fable_without_fetching(self):
        pyccsl.write_json_atomic(pyccsl.usage_cache_path(), {
            "attempted_at": self.NOW - 10, "fetched_at": self.NOW - 10, "fable": {"percent": 11}})
        metrics = pyccsl.collect_cache_and_usage({}, ["usage-fable"], self.NOW, self.spawn)
        self.assertEqual(metrics["usage"]["usage-fable"], 11)
        self.assertEqual(self.spawned, [])

    def test_empty_cache_starts_a_fetch(self):
        metrics = pyccsl.collect_cache_and_usage({}, ["usage-fable"], self.NOW, self.spawn)
        self.assertEqual(self.spawned, [True])
        self.assertNotIn("usage-fable", metrics["usage"])

    def test_spawn_failure_does_not_lose_other_usage_fields(self):
        def raising_spawn():
            raise OSError("boom")

        data = {"rate_limits": {"five_hour": {"used_percentage": 7}}}
        metrics = pyccsl.collect_cache_and_usage(data, ["usage-5h", "usage-fable"], self.NOW, raising_spawn)
        self.assertEqual(metrics["usage"], {"usage-5h": 7})


if __name__ == "__main__":
    unittest.main()
