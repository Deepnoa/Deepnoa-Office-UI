"""
Tests for RunsAdapter in backend/services/source_adapters.py.

Run from the backend/ directory:
    python -m pytest tests/test_runs_adapter.py -v
or:
    python -m unittest tests.test_runs_adapter -v

Architecture under test:
    RunsAdapter._call()
        → GET /plugins/run-viewer/runs?text=<args>   (mocked by _requests_lib)
        → OpenClaw gateway :19001 (Bearer token auth)
        → run-viewer plugin

All tests mock _requests_lib so no live gateway is required.
"""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Allow running from repo root or from backend/
_backend_dir = os.path.join(os.path.dirname(__file__), "..")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.source_adapters import RunsAdapter


# ── Helpers ────────────────────────────────────────────────────────────────────

_GATEWAY_URL = "http://localhost:19001"
_GATEWAY_TOKEN = "test-token-abc"


def _make_adapter() -> RunsAdapter:
    return RunsAdapter(gateway_url=_GATEWAY_URL, gateway_token=_GATEWAY_TOKEN)


def _mock_ok_response(text: str) -> MagicMock:
    """Build a mock requests.Response for a successful gateway call."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "text": text, "command": "/runs test"}
    resp.raise_for_status.return_value = None
    return resp


# ── Sample raw texts ───────────────────────────────────────────────────────────

_RAW_RECENT = (
    "*直近の run 記録 (3件)*\n"
    "✅ `run_20260420_081200_abc` `health` 2026-04-20 08:12:00 (1.2s)\n"
    "❌ `run_20260419_075500_def` `digest` 2026-04-19 07:55:00 \u2014 \u26a0\ufe0f timeout\n"
    "⏳ `run_20260419_070000_ghi` `health` 2026-04-19 07:00:00 (50ms) \u21a9\n"
    "\n"
    "_詳細: `/runs <run_id>`_"
)

_RAW_RECENT_EMPTY = "実行記録が見つかりません。"

_RAW_HEALTH_SINGLE = (
    "\u2705 *run health (2026-04-20)*  \u00b7 Asia/Tokyo\n"
    "status: ok\n"
    "done: 6 | failed: 0 | running: 0 | queued: 0 | cancelled: 0 | total: 6\n"
)

_RAW_HEALTH_MULTI = (
    "\u26a0\ufe0f *run health (2026-04-14..2026-04-20)*  \u00b7 Asia/Tokyo\n"
    "status: degraded \u00b7 failed=1\n"
    "done: 5 | failed: 1 | running: 0 | queued: 0 | cancelled: 0 | total: 6\n"
    "\n"
    "*daily breakdown:*\n"
    "\u26a0\ufe0f 2026-04-20  failed=1\n"
    "\u2705 2026-04-19  done=3\n"
    "\u2705 2026-04-18  no runs\n"
)

_RAW_HEALTH_UTC = (
    "\u2705 *run health (2026-04-20)*  \u00b7 UTC\n"
    "status: ok\n"
    "done: 2 | failed: 0 | running: 0 | queued: 0 | cancelled: 0 | total: 2\n"
)

_RAW_HEALTH_NO_TZ = (
    "\u2705 *run health (2026-04-20)*\n"
    "status: ok\n"
    "done: 1 | failed: 0 | running: 0 | queued: 0 | cancelled: 0 | total: 1\n"
)

_RAW_DETAIL = (
    "\u2705 *run 詳細: `run_20260420_081200_abc`*\n"
    "種別: `health`\u3000状態: `done`\n"
    "受付: 2026-04-20 08:12:00 (1.2s)\n"
    "開始: 08:12:01\n"
    "完了: 08:13:20\n"
    "タスク: `daily health check`\n"
    "\n"
    "_一覧: `/runs`_"
)

_RAW_DETAIL_NOT_FOUND = "実行記録が見つかりません: `run_unknown`"


# ── Fetch tests ────────────────────────────────────────────────────────────────

class TestRunsAdapterFetch(unittest.TestCase):

    @patch("services.source_adapters._requests_lib")
    def test_fetch_recent_default_limit(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_RECENT)
        adapter = _make_adapter()
        result = adapter.fetch_recent()
        mock_req.get.assert_called_once()
        url: str = mock_req.get.call_args[0][0]
        # urllib.parse.quote("last=10") → "last%3D10"
        self.assertIn("last%3D10", url)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_recent_custom_limit(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_RECENT)
        adapter = _make_adapter()
        adapter.fetch_recent(5)
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("last%3D5", url)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_recent_returns_raw_text(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_RECENT)
        adapter = _make_adapter()
        result = adapter.fetch_recent()
        self.assertEqual(result, _RAW_RECENT)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_health_today(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_HEALTH_SINGLE)
        adapter = _make_adapter()
        adapter.fetch_health()
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("text=health", url)
        # Must NOT have a date suffix when no date_spec given
        self.assertNotIn("health+", url)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_health_rolling_window(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_HEALTH_MULTI)
        adapter = _make_adapter()
        adapter.fetch_health("7d")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("health", url)
        self.assertIn("7d", url)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_health_date_range(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_HEALTH_MULTI)
        adapter = _make_adapter()
        adapter.fetch_health("2026-04-01..2026-04-20")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("2026-04-01", url)
        self.assertIn("2026-04-20", url)

    @patch("services.source_adapters._requests_lib")
    def test_fetch_detail_encodes_run_id(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response(_RAW_DETAIL)
        adapter = _make_adapter()
        adapter.fetch_detail("run_20260420_081200_abc")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("run_20260420_081200_abc", url)

    @patch("services.source_adapters._requests_lib")
    def test_bearer_token_in_auth_header(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response("text")
        adapter = _make_adapter()
        adapter.fetch_recent()
        headers: dict = mock_req.get.call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {_GATEWAY_TOKEN}")

    @patch("services.source_adapters._requests_lib")
    def test_timeout_forwarded(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response("text")
        adapter = RunsAdapter(gateway_url=_GATEWAY_URL, gateway_token=_GATEWAY_TOKEN, timeout=3)
        adapter.fetch_recent()
        timeout = mock_req.get.call_args[1]["timeout"]
        self.assertEqual(timeout, 3)

    @patch("services.source_adapters._requests_lib")
    def test_gateway_url_base_strip(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_ok_response("text")
        adapter = RunsAdapter(
            gateway_url="http://localhost:19001/",  # trailing slash
            gateway_token=_GATEWAY_TOKEN,
        )
        adapter.fetch_recent()
        url: str = mock_req.get.call_args[0][0]
        # Should not result in double slash before "plugins"
        self.assertNotIn("//plugins", url)
        self.assertIn("/plugins/run-viewer/runs", url)


# ── parse_recent tests ─────────────────────────────────────────────────────────

class TestParseRecent(unittest.TestCase):

    def setUp(self) -> None:
        self.result = RunsAdapter.parse_recent(_RAW_RECENT)

    def test_returns_three_runs(self) -> None:
        self.assertEqual(len(self.result["runs"]), 3)

    def test_raw_text_preserved(self) -> None:
        self.assertEqual(self.result["raw_text"], _RAW_RECENT)

    def test_first_run_id(self) -> None:
        self.assertEqual(self.result["runs"][0]["run_id"], "run_20260420_081200_abc")

    def test_first_run_kind(self) -> None:
        self.assertEqual(self.result["runs"][0]["kind"], "health")

    def test_first_run_status_done(self) -> None:
        self.assertEqual(self.result["runs"][0]["status"], "done")

    def test_first_run_date(self) -> None:
        self.assertEqual(self.result["runs"][0]["date"], "2026-04-20")

    def test_first_run_time(self) -> None:
        self.assertEqual(self.result["runs"][0]["time"], "08:12:00")

    def test_first_run_elapsed(self) -> None:
        self.assertEqual(self.result["runs"][0]["elapsed"], "1.2s")

    def test_first_run_not_retry(self) -> None:
        self.assertFalse(self.result["runs"][0]["retry"])

    def test_second_run_status_failed(self) -> None:
        self.assertEqual(self.result["runs"][1]["status"], "failed")

    def test_second_run_summary_contains_timeout(self) -> None:
        summary = self.result["runs"][1]["summary"] or ""
        self.assertIn("timeout", summary)

    def test_third_run_status_queued(self) -> None:
        self.assertEqual(self.result["runs"][2]["status"], "queued")

    def test_third_run_elapsed_ms(self) -> None:
        self.assertEqual(self.result["runs"][2]["elapsed"], "50ms")

    def test_third_run_is_retry(self) -> None:
        self.assertTrue(self.result["runs"][2]["retry"])

    def test_empty_text_returns_zero_runs(self) -> None:
        result = RunsAdapter.parse_recent(_RAW_RECENT_EMPTY)
        self.assertEqual(result["runs"], [])
        self.assertEqual(result["raw_text"], _RAW_RECENT_EMPTY)


# ── parse_health tests ─────────────────────────────────────────────────────────

class TestParseHealthSingleDay(unittest.TestCase):

    def setUp(self) -> None:
        self.result = RunsAdapter.parse_health(_RAW_HEALTH_SINGLE)

    def test_date(self) -> None:
        self.assertEqual(self.result["date"], "2026-04-20")

    def test_timezone(self) -> None:
        self.assertEqual(self.result["time_zone"], "Asia/Tokyo")

    def test_status_ok(self) -> None:
        self.assertEqual(self.result["status"], "ok")

    def test_counts_done(self) -> None:
        self.assertEqual(self.result["counts"]["done"], 6)

    def test_counts_failed_zero(self) -> None:
        self.assertEqual(self.result["counts"]["failed"], 0)

    def test_counts_total(self) -> None:
        self.assertEqual(self.result["counts"]["total"], 6)

    def test_no_daily_breakdown(self) -> None:
        self.assertEqual(self.result["daily"], [])

    def test_raw_text_preserved(self) -> None:
        self.assertEqual(self.result["raw_text"], _RAW_HEALTH_SINGLE)


class TestParseHealthMultiDay(unittest.TestCase):

    def setUp(self) -> None:
        self.result = RunsAdapter.parse_health(_RAW_HEALTH_MULTI)

    def test_date_range(self) -> None:
        self.assertEqual(self.result["date"], "2026-04-14..2026-04-20")

    def test_timezone(self) -> None:
        self.assertEqual(self.result["time_zone"], "Asia/Tokyo")

    def test_status_degraded(self) -> None:
        self.assertEqual(self.result["status"], "degraded")

    def test_counts_failed(self) -> None:
        self.assertEqual(self.result["counts"]["failed"], 1)

    def test_counts_done(self) -> None:
        self.assertEqual(self.result["counts"]["done"], 5)

    def test_three_daily_entries(self) -> None:
        self.assertEqual(len(self.result["daily"]), 3)

    def test_daily_first_failed(self) -> None:
        first = self.result["daily"][0]
        self.assertEqual(first["date"], "2026-04-20")
        self.assertEqual(first["failed"], 1)
        self.assertEqual(first["done"], 0)
        self.assertFalse(first["no_runs"])

    def test_daily_second_done(self) -> None:
        second = self.result["daily"][1]
        self.assertEqual(second["date"], "2026-04-19")
        self.assertEqual(second["done"], 3)
        self.assertFalse(second["no_runs"])

    def test_daily_third_no_runs(self) -> None:
        third = self.result["daily"][2]
        self.assertEqual(third["date"], "2026-04-18")
        self.assertTrue(third["no_runs"])

    def test_raw_text_preserved(self) -> None:
        self.assertEqual(self.result["raw_text"], _RAW_HEALTH_MULTI)


class TestParseHealthEdgeCases(unittest.TestCase):

    def test_utc_timezone(self) -> None:
        result = RunsAdapter.parse_health(_RAW_HEALTH_UTC)
        self.assertEqual(result["time_zone"], "UTC")

    def test_no_timezone_returns_none(self) -> None:
        result = RunsAdapter.parse_health(_RAW_HEALTH_NO_TZ)
        self.assertIsNone(result["time_zone"])

    def test_empty_text_returns_default_structure(self) -> None:
        result = RunsAdapter.parse_health("")
        self.assertIsNone(result["date"])
        self.assertIsNone(result["status"])
        self.assertEqual(result["counts"]["total"], 0)
        self.assertEqual(result["daily"], [])


# ── parse_detail tests ─────────────────────────────────────────────────────────

class TestParseDetail(unittest.TestCase):

    def setUp(self) -> None:
        self.result = RunsAdapter.parse_detail(_RAW_DETAIL)

    def test_run_id(self) -> None:
        self.assertEqual(self.result["run_id"], "run_20260420_081200_abc")

    def test_kind(self) -> None:
        self.assertEqual(self.result["kind"], "health")

    def test_status_done(self) -> None:
        self.assertEqual(self.result["status"], "done")

    def test_queued_date(self) -> None:
        self.assertEqual(self.result["queued_date"], "2026-04-20")

    def test_queued_time(self) -> None:
        self.assertEqual(self.result["queued_time"], "08:12:00")

    def test_raw_text_preserved(self) -> None:
        self.assertEqual(self.result["raw_text"], _RAW_DETAIL)

    def test_not_found_returns_nones(self) -> None:
        result = RunsAdapter.parse_detail(_RAW_DETAIL_NOT_FOUND)
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["kind"])
        self.assertIsNone(result["status"])
        self.assertIsNone(result["queued_date"])


# ── fetch_recent_json / fetch_detail_json tests ────────────────────────────────

_FULL_RUN_RECORD = {
    "run_id": "run_20260420_081200_abc",
    "requested_by": "U_TEST",
    "kind": "health",
    "status": "done",
    "queued_at": "2026-04-20T08:12:00Z",
    "started_at": "2026-04-20T08:12:01Z",
    "done_at": "2026-04-20T08:13:20Z",
    "result": {
        "summary": "All systems healthy.",
        "key_points": ["CPU ok", "Memory ok"],
        "exit_code": 0,
    },
    "error": None,
    "retry_of": None,
    "retry_count": 0,
}


def _mock_json_list_response(runs: list) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "runs": runs, "total": len(runs)}
    resp.raise_for_status.return_value = None
    return resp


def _mock_json_detail_response(run: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "run": run}
    resp.raise_for_status.return_value = None
    return resp


def _mock_404_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 404
    exc = _requests_lib_module().exceptions.HTTPError(response=resp) if False else MagicMock()
    # Build a real-ish HTTPError with response attached
    import requests as _r
    http_exc = _r.exceptions.HTTPError()
    http_exc.response = MagicMock()
    http_exc.response.status_code = 404
    resp.raise_for_status.side_effect = http_exc
    return resp


class TestFetchRecentJson(unittest.TestCase):

    @patch("services.source_adapters._requests_lib")
    def test_returns_list_of_records(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([_FULL_RUN_RECORD])
        result = _make_adapter().fetch_recent_json()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["run_id"], "run_20260420_081200_abc")

    @patch("services.source_adapters._requests_lib")
    def test_url_contains_format_json(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        _make_adapter().fetch_recent_json()
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("format=json", url)
        self.assertIn("mode=list", url)

    @patch("services.source_adapters._requests_lib")
    def test_limit_param_forwarded(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        _make_adapter().fetch_recent_json(limit=7)
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("limit=7", url)

    @patch("services.source_adapters._requests_lib")
    def test_status_param_forwarded(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        _make_adapter().fetch_recent_json(status="failed")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("status=failed", url)

    @patch("services.source_adapters._requests_lib")
    def test_date_param_forwarded(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        _make_adapter().fetch_recent_json(date="2026-04-20")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("date=2026-04-20", url)

    @patch("services.source_adapters._requests_lib")
    def test_no_status_no_date_omits_params(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        _make_adapter().fetch_recent_json()
        url: str = mock_req.get.call_args[0][0]
        self.assertNotIn("status=", url)
        self.assertNotIn("date=", url)

    @patch("services.source_adapters._requests_lib")
    def test_empty_runs_returns_empty_list(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([])
        result = _make_adapter().fetch_recent_json()
        self.assertEqual(result, [])

    @patch("services.source_adapters._requests_lib")
    def test_full_record_fields_preserved(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_list_response([_FULL_RUN_RECORD])
        result = _make_adapter().fetch_recent_json()
        rec = result[0]
        self.assertEqual(rec["queued_at"], "2026-04-20T08:12:00Z")
        self.assertEqual(rec["result"]["summary"], "All systems healthy.")
        self.assertIsNone(rec["error"])


class TestFetchDetailJson(unittest.TestCase):

    @patch("services.source_adapters._requests_lib")
    def test_returns_run_dict(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_detail_response(_FULL_RUN_RECORD)
        result = _make_adapter().fetch_detail_json("run_20260420_081200_abc")
        self.assertIsNotNone(result)
        self.assertEqual(result["run_id"], "run_20260420_081200_abc")

    @patch("services.source_adapters._requests_lib")
    def test_url_contains_format_json_and_mode_detail(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_detail_response(_FULL_RUN_RECORD)
        _make_adapter().fetch_detail_json("run_20260420_081200_abc")
        url: str = mock_req.get.call_args[0][0]
        self.assertIn("format=json", url)
        self.assertIn("mode=detail", url)
        self.assertIn("run_id=run_20260420_081200_abc", url)

    @patch("services.source_adapters._requests_lib")
    def test_not_found_returns_none(self, mock_req: MagicMock) -> None:
        import requests as _r
        http_exc = _r.exceptions.HTTPError()
        http_exc.response = MagicMock()
        http_exc.response.status_code = 404
        mock_req.exceptions.HTTPError = _r.exceptions.HTTPError
        resp = MagicMock()
        resp.raise_for_status.side_effect = http_exc
        mock_req.get.return_value = resp
        result = _make_adapter().fetch_detail_json("run_notexist_000")
        self.assertIsNone(result)

    @patch("services.source_adapters._requests_lib")
    def test_full_record_fields_preserved(self, mock_req: MagicMock) -> None:
        mock_req.get.return_value = _mock_json_detail_response(_FULL_RUN_RECORD)
        result = _make_adapter().fetch_detail_json("run_20260420_081200_abc")
        self.assertEqual(result["started_at"], "2026-04-20T08:12:01Z")
        self.assertEqual(result["done_at"], "2026-04-20T08:13:20Z")
        self.assertEqual(result["result"]["key_points"], ["CPU ok", "Memory ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
