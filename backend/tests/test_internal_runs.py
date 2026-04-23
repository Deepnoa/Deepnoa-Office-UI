"""
Tests for the /api/internal/runs, /api/internal/health, and
/api/internal/runs/<run_id> endpoints in backend/app.py.

Verifies:
  - RunsAdapter is used as the primary data source when configured
  - Fallback to file-read helpers when adapter is None or raises
  - Response shape is maintained for frontend compatibility
  - limit/status/date query params are forwarded correctly

Run from the backend/ directory:
    python -m pytest tests/test_internal_runs.py -v
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Allow running from repo root or from backend/
_backend_dir = os.path.join(os.path.dirname(__file__), "..")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


# ── Stub heavy optional deps before importing app ─────────────────────────────

def _stub_modules():
    """Stub modules that pull in C-extensions or touch the filesystem."""
    for mod_name in [
        "PIL", "PIL.Image",
        "security_utils",
        "memo_utils",
        "store_utils",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    # security_utils needs specific attrs
    sec = sys.modules["security_utils"]
    for fn in ("is_production_mode", "is_strong_secret", "is_strong_drawer_pass"):
        if not hasattr(sec, fn):
            setattr(sec, fn, lambda *a, **kw: False)

    # memo_utils
    memo = sys.modules["memo_utils"]
    for fn in ("get_yesterday_date_str", "sanitize_content", "extract_memo_from_file"):
        if not hasattr(memo, fn):
            setattr(memo, fn, lambda *a, **kw: "")

    # store_utils — all load/save helpers
    store = sys.modules["store_utils"]
    for fn in (
        "load_agents_state", "save_agents_state",
        "load_asset_positions", "save_asset_positions",
        "load_asset_defaults", "save_asset_defaults",
        "load_runtime_config", "save_runtime_config",
        "load_join_keys", "save_join_keys",
    ):
        if not hasattr(store, fn):
            setattr(store, fn, lambda *a, **kw: {})


_stub_modules()

# Patch services that read from disk before importing app
_bridge_mock = MagicMock()
_bridge_mock.build_internal_state_payload = MagicMock(return_value={})
_bridge_mock.build_openclaw_bridge_snapshot = MagicMock(return_value={})
_bridge_mock.build_public_state_payload = MagicMock(return_value={})

_schemas_mock = MagicMock()
_schemas_mock.DEPRECATED_ROUTE_META = {}
_schemas_mock.EVENT_HISTORY_RETENTION = 100
_schemas_mock.build_events_contract = MagicMock(return_value={})
_schemas_mock.normalize_event_payload = MagicMock(return_value={})
_schemas_mock.normalize_internal_state = MagicMock(return_value={})

sys.modules.setdefault("services.openclaw_bridge", _bridge_mock)
sys.modules.setdefault("services.schemas", _schemas_mock)

import app as _app_module  # noqa: E402 — must come after stubs

flask_app = _app_module.app
flask_app.config["TESTING"] = True


# ── Shared fixtures ────────────────────────────────────────────────────────────

_SAMPLE_RUN = {
    "run_id": "run_20260421_120000_abc",
    "kind": "digest",
    "status": "done",
    "queued_at": "2026-04-21T12:00:00Z",
    "started_at": "2026-04-21T12:00:01Z",
    "done_at": "2026-04-21T12:00:05Z",
    "result": {"summary": "All good", "key_points": ["point A"]},
    "error": None,
    "retry_of": None,
    "channel_id": "CH001",
}

_SAMPLE_FAILED_RUN = {
    "run_id": "run_20260421_110000_xyz",
    "kind": "digest",
    "status": "failed",
    "queued_at": "2026-04-21T11:00:00Z",
    "started_at": "2026-04-21T11:00:01Z",
    "done_at": None,
    "result": None,
    "error": {"message": "Connection refused"},
    "retry_of": None,
    "channel_id": "CH001",
}

_MINIMAL_INTERNAL_STATE = {
    "connectors": [],
    "alerts": [],
    "roles": [],
    "summary": {},
    "policies": {"connector_health_rules": {}},
}


def _build_view_state_mock(return_value=None):
    return MagicMock(return_value=return_value or _MINIMAL_INTERNAL_STATE)


# ── /api/internal/runs ─────────────────────────────────────────────────────────

class TestApiInternalRuns(unittest.TestCase):
    """Tests for GET /api/internal/runs."""

    def setUp(self):
        self.client = flask_app.test_client()

    # -- Adapter path ----------------------------------------------------------

    def test_adapter_primary_returns_records(self):
        """When adapter succeeds, its records are returned."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = [_SAMPLE_RUN]

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            resp = self.client.get("/api/internal/runs")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["runs"], [_SAMPLE_RUN])
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["schema_version"], "2026-04-14")

    def test_adapter_receives_limit_param(self):
        """limit query param is forwarded to adapter."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = []

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            self.client.get("/api/internal/runs?limit=25")

        adapter.fetch_recent_json.assert_called_once_with(
            limit=25, status=None, date=None
        )

    def test_adapter_receives_status_and_date_params(self):
        """status and date query params are forwarded to adapter."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = []

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            self.client.get("/api/internal/runs?status=failed&date=2026-04-21")

        adapter.fetch_recent_json.assert_called_once_with(
            limit=_app_module._RUNS_LIST_LIMIT_DEFAULT,
            status="failed",
            date="2026-04-21",
        )

    def test_limit_is_clamped_to_max(self):
        """limit is clamped to _RUNS_LIST_LIMIT_MAX."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = []

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            self.client.get(f"/api/internal/runs?limit=9999")

        call_kwargs = adapter.fetch_recent_json.call_args
        self.assertEqual(call_kwargs.kwargs["limit"], _app_module._RUNS_LIST_LIMIT_MAX)

    # -- Fallback path ---------------------------------------------------------

    def test_fallback_when_adapter_is_none(self):
        """When _RUNS_ADAPTER is None, _load_runs() is used."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_load_runs", return_value=[_SAMPLE_RUN]) as mock_load:
            resp = self.client.get("/api/internal/runs")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["runs"], [_SAMPLE_RUN])
        mock_load.assert_called_once()

    def test_fallback_when_adapter_raises(self):
        """When adapter raises, _load_runs() is used as fallback."""
        adapter = MagicMock()
        adapter.fetch_recent_json.side_effect = OSError("gateway down")

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch.object(_app_module, "_load_runs", return_value=[_SAMPLE_RUN]) as mock_load:
            resp = self.client.get("/api/internal/runs")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["runs"], [_SAMPLE_RUN])
        mock_load.assert_called_once()

    def test_fallback_load_runs_receives_params(self):
        """When falling back, limit/status/date are forwarded to _load_runs."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_load_runs", return_value=[]) as mock_load:
            self.client.get("/api/internal/runs?limit=10&status=done&date=2026-04-20")

        mock_load.assert_called_once_with(limit=10, status_filter="done", date_filter="2026-04-20")

    # -- Response shape --------------------------------------------------------

    def test_response_shape(self):
        """Response always has ok/schema_version/runs/total/limit keys."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_load_runs", return_value=[]):
            resp = self.client.get("/api/internal/runs")

        data = resp.get_json()
        for key in ("ok", "schema_version", "runs", "total", "limit"):
            self.assertIn(key, data, f"Missing key: {key}")
        self.assertEqual(data["schema_version"], "2026-04-14")


# ── /api/internal/health ──────────────────────────────────────────────────────

class TestApiInternalHealth(unittest.TestCase):
    """Tests for GET /api/internal/health."""

    def setUp(self):
        self.client = flask_app.test_client()
        self._view_state_patch = patch.object(
            _app_module, "build_internal_view_state",
            _build_view_state_mock(),
        )
        self._view_state_patch.start()

    def tearDown(self):
        self._view_state_patch.stop()

    # -- Adapter path ----------------------------------------------------------

    def test_adapter_used_for_today_runs(self):
        """When adapter succeeds, today_runs comes from adapter."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = [_SAMPLE_RUN, _SAMPLE_FAILED_RUN]

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch("app._health_load_runs_today") as mock_file:
            resp = self.client.get("/api/internal/health")

        mock_file.assert_not_called()
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["runs_today"]["done"], 1)
        self.assertEqual(data["runs_today"]["failed"], 1)
        self.assertEqual(data["runs_today"]["total"], 2)

    def test_adapter_receives_date_today(self):
        """Adapter is called with date=today."""
        from datetime import date as _date
        today = _date.today().isoformat()
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = []

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            self.client.get("/api/internal/health")

        call_kwargs = adapter.fetch_recent_json.call_args.kwargs
        self.assertEqual(call_kwargs["date"], today)

    def test_latest_failed_from_adapter(self):
        """latest_failed_run is populated from the first failed run in adapter result."""
        adapter = MagicMock()
        adapter.fetch_recent_json.return_value = [_SAMPLE_FAILED_RUN]

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            resp = self.client.get("/api/internal/health")

        data = resp.get_json()
        lf = data["latest_failed_run"]
        self.assertIsNotNone(lf)
        self.assertEqual(lf["run_id"], "run_20260421_110000_xyz")
        self.assertEqual(lf["error_message"], "Connection refused")

    # -- Fallback path ---------------------------------------------------------

    def test_fallback_when_adapter_is_none(self):
        """When _RUNS_ADAPTER is None, _health_load_runs_today() is used."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch("app._health_load_runs_today", return_value=[_SAMPLE_RUN]) as mock_file:
            resp = self.client.get("/api/internal/health")

        mock_file.assert_called_once()
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["runs_today"]["done"], 1)

    def test_fallback_when_adapter_raises(self):
        """When adapter raises, _health_load_runs_today() is used."""
        adapter = MagicMock()
        adapter.fetch_recent_json.side_effect = TimeoutError("gateway timeout")

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch("app._health_load_runs_today", return_value=[]) as mock_file:
            resp = self.client.get("/api/internal/health")

        mock_file.assert_called_once()
        self.assertEqual(resp.status_code, 200)

    # -- Response shape --------------------------------------------------------

    def test_response_shape_keys(self):
        """Response has all required top-level keys."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch("app._health_load_runs_today", return_value=[]):
            resp = self.client.get("/api/internal/health")

        data = resp.get_json()
        for key in (
            "ok", "generated_at", "overall_status",
            "runs_today", "latest_failed_run",
            "connectors", "connector_total", "alerts", "roles", "summary",
        ):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_runs_today_shape(self):
        """runs_today has date + all status counts + total."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch("app._health_load_runs_today", return_value=[]):
            resp = self.client.get("/api/internal/health")

        rt = resp.get_json()["runs_today"]
        for key in ("date", "done", "running", "failed", "queued", "cancelled", "total"):
            self.assertIn(key, rt, f"Missing runs_today key: {key}")

    def test_overall_status_degraded_on_failed(self):
        """overall_status is 'degraded' when there are failed runs."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch("app._health_load_runs_today", return_value=[_SAMPLE_FAILED_RUN]):
            resp = self.client.get("/api/internal/health")

        self.assertEqual(resp.get_json()["overall_status"], "degraded")

    def test_overall_status_ok_when_no_issues(self):
        """overall_status is 'ok' when no failed runs and no bad connectors."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch("app._health_load_runs_today", return_value=[_SAMPLE_RUN]):
            resp = self.client.get("/api/internal/health")

        self.assertEqual(resp.get_json()["overall_status"], "ok")


# ── /api/internal/runs/<run_id> ───────────────────────────────────────────────

class TestApiInternalRunDetail(unittest.TestCase):
    """Tests for GET /api/internal/runs/<run_id>."""

    def setUp(self):
        self.client = flask_app.test_client()
        self._run_id = "run_20260421_120000_abc"

    # -- Validation ------------------------------------------------------------

    def test_invalid_run_id_returns_400(self):
        """Malformed run_id returns 400."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None):
            resp = self.client.get("/api/internal/runs/not-a-valid-id")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_run_id")

    # -- Adapter path ----------------------------------------------------------

    def test_adapter_primary_returns_run(self):
        """When adapter returns a record, it is used directly."""
        adapter = MagicMock()
        adapter.fetch_detail_json.return_value = _SAMPLE_RUN

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["run"], _SAMPLE_RUN)

    def test_adapter_called_with_run_id(self):
        """Adapter is called with the correct run_id."""
        adapter = MagicMock()
        adapter.fetch_detail_json.return_value = _SAMPLE_RUN

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            self.client.get(f"/api/internal/runs/{self._run_id}")

        adapter.fetch_detail_json.assert_called_once_with(self._run_id)

    def test_file_read_not_called_when_adapter_succeeds(self):
        """_find_run_file is NOT called when adapter returns a record."""
        adapter = MagicMock()
        adapter.fetch_detail_json.return_value = _SAMPLE_RUN

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch.object(_app_module, "_find_run_file") as mock_find:
            self.client.get(f"/api/internal/runs/{self._run_id}")

        mock_find.assert_not_called()

    # -- Fallback path ---------------------------------------------------------

    def test_fallback_when_adapter_is_none(self):
        """When _RUNS_ADAPTER is None, file-read path is used."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_find_run_file", return_value="/fake/path.json"), \
             patch.object(_app_module, "_read_run_json", return_value=_SAMPLE_RUN):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["run"], _SAMPLE_RUN)

    def test_fallback_when_adapter_raises(self):
        """When adapter raises, file-read path is used."""
        adapter = MagicMock()
        adapter.fetch_detail_json.side_effect = ConnectionError("gateway unreachable")

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch.object(_app_module, "_find_run_file", return_value="/fake/path.json"), \
             patch.object(_app_module, "_read_run_json", return_value=_SAMPLE_RUN):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["run"], _SAMPLE_RUN)

    def test_404_when_adapter_returns_none_and_file_missing(self):
        """When adapter returns None (404) and file-read also misses, return 404."""
        adapter = MagicMock()
        adapter.fetch_detail_json.return_value = None  # gateway 404

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter), \
             patch.object(_app_module, "_find_run_file", return_value=None):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "not_found")

    def test_404_when_adapter_none_and_file_missing(self):
        """When adapter is None and file is missing, return 404."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_find_run_file", return_value=None):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 404)

    def test_500_when_file_unreadable(self):
        """When file exists but is unreadable, return 500."""
        with patch.object(_app_module, "_RUNS_ADAPTER", None), \
             patch.object(_app_module, "_find_run_file", return_value="/fake/path.json"), \
             patch.object(_app_module, "_read_run_json", return_value=None):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json()["error"], "unreadable")

    # -- Response shape --------------------------------------------------------

    def test_response_shape(self):
        """Successful response has ok and run keys."""
        adapter = MagicMock()
        adapter.fetch_detail_json.return_value = _SAMPLE_RUN

        with patch.object(_app_module, "_RUNS_ADAPTER", adapter):
            resp = self.client.get(f"/api/internal/runs/{self._run_id}")

        data = resp.get_json()
        self.assertIn("ok", data)
        self.assertIn("run", data)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
