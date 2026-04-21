from __future__ import annotations

import re
import urllib.parse

try:
    import requests as _requests_lib  # type: ignore[import]
except ImportError:
    _requests_lib = None  # type: ignore[assignment]

from .schemas import WORKSPACE_ID, normalize_event_payload, normalize_provenance


def manager_activity_events(manager_state: dict) -> list[dict]:
    events = []
    for item in list(manager_state.get("activity") or []):
        payload = dict(item)
        payload.setdefault("agent_id", item.get("role"))
        payload["provenance"] = normalize_provenance(item.get("provenance") or "actual")
        events.append(normalize_event_payload(payload, workspace_id=WORKSPACE_ID))
    return events


def intake_events(intake: list[dict]) -> list[dict]:
    events = []
    for item in list(intake or []):
        payload = {
            "event_type": "channel.message.received",
            "timestamp": item.get("updated_at"),
            "workspace_id": WORKSPACE_ID,
            "source": "public",
            "agent_id": item.get("role"),
            "task_id": item.get("id"),
            "summary": item.get("summary"),
            "severity": "info",
            "provenance": "actual",
            "raw_item": item,
        }
        events.append(normalize_event_payload(payload, workspace_id=WORKSPACE_ID))
    return events


def snapshot_events(primary_state: dict, normalized_agents: list[dict]) -> list[dict]:
    events = []
    if primary_state:
        events.append(normalize_event_payload({
            "event_type": "agent.status.changed",
            "source": "state-file",
            "agent_id": "main",
            "state": primary_state.get("state"),
            "timestamp": primary_state.get("updated_at"),
            "detail": primary_state.get("detail"),
            "provenance": "derived",
        }, workspace_id=WORKSPACE_ID))
    for agent in normalized_agents:
        events.append(normalize_event_payload({
            "event_type": "agent.status.changed",
            "source": agent["source"] or "agents-state",
            "agent_id": agent["agent_id"],
            "state": agent["state"],
            "timestamp": agent["last_push_at"] or agent["updated_at"],
            "detail": agent["name"],
            "provenance": "derived",
        }, workspace_id=WORKSPACE_ID))
    return events


def derive_missing_lifecycle(events: list[dict]) -> list[dict]:
    by_task = {}
    for event in sorted(events, key=lambda item: str(item.get("timestamp") or "")):
        task_id = event.get("task_id") or ""
        if not task_id:
            continue
        by_task.setdefault(task_id, []).append(event)

    derived = []
    for task_id, task_events in by_task.items():
        types = {item.get("event_type") for item in task_events}
        first = task_events[0]
        if "task.started" in types and "task.created" not in types:
            derived.append(normalize_event_payload({
                "event_type": "task.created",
                "source": first.get("source"),
                "agent_id": first.get("agent_id"),
                "task_id": task_id,
                "summary": first.get("display_summary"),
                "timestamp": first.get("timestamp"),
                "state": "executing",
                "provenance": "backfilled",
            }, workspace_id=WORKSPACE_ID))
        if "task.started" in types and "task.assigned" not in types:
            derived.append(normalize_event_payload({
                "event_type": "task.assigned",
                "source": first.get("source"),
                "agent_id": first.get("agent_id"),
                "task_id": task_id,
                "summary": first.get("display_summary"),
                "timestamp": first.get("timestamp"),
                "state": "executing",
                "provenance": "backfilled",
            }, workspace_id=WORKSPACE_ID))
    return derived


def dedupe_events(events: list[dict]) -> list[dict]:
    ranked = sorted(
        list(events or []),
        key=lambda item: (
            str(item.get("timestamp") or ""),
            0 if item.get("provenance") == "actual" else 1 if item.get("provenance") == "derived" else 2,
        ),
        reverse=True,
    )
    seen = set()
    result = []
    for event in ranked:
        dedupe_key = (
            event.get("event_type"),
            event.get("agent_id"),
            event.get("task_id"),
            event.get("timestamp"),
            event.get("approval_status"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(event)
    result.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return result


# ── RunsAdapter ────────────────────────────────────────────────────────────────


class RunsAdapter:
    """HTTP client for run-viewer data routed through the OpenClaw gateway.

    Data path (internal-state only, never public):
        RunsAdapter._call()
            → GET /plugins/run-viewer/runs?text=<args>
            → OpenClaw gateway :19001  (Bearer token auth)
            → run-viewer plugin
            → ~/.openclaw/runs/<date>/<run_id>.json

    Usage::

        adapter = RunsAdapter(
            gateway_url="http://localhost:19001",
            gateway_token=os.environ["OPENCLAW_GATEWAY_TOKEN"],
        )
        raw = adapter.fetch_recent(10)
        structured = RunsAdapter.parse_recent(raw)

    The three ``fetch_*`` methods return the raw display text produced by the
    run-viewer plugin (suitable for direct rendering or logging).  The three
    ``parse_*`` staticmethods extract structurally stable fields from that text
    (run_id, kind, status, timestamps) and preserve ``raw_text`` so that callers
    can fall back to the full string for any field not yet parsed.  This keeps
    the parse layer forward-compatible as the display format evolves.
    """

    # ── Compiled patterns ──────────────────────────────────────────────────────

    # Run list line: emoji `run_id` `kind` YYYY-MM-DD HH:MM:SS (elapsed?) ↩? — summary?
    _RUN_LINE_RE = re.compile(
        r"`(run_[A-Za-z0-9_]+)`\s+`([^`]+)`\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})"
        r"(?:\s+\(([^)]+)\))?"          # optional elapsed: (1.2s) / (100ms)
        r"(?:\s+(\u21a9))?"             # optional retry marker ↩ (U+21A9)
        r"(?:\s+\u2014\s+(.+))?",       # optional summary after — (U+2014 em dash)
    )

    # Health header: emoji *run health (date-or-range)*  · TimeZone
    # Handles U+00B7 (·), U+2022 (•), U+2014 (—) as separators.
    _HEALTH_HEADER_RE = re.compile(
        r"\*run health \(([^)]+)\)\*"
        r"(?:\s*[\u00b7\u2022\u2014]\s*(\S+))?",   # optional separator + IANA tz token
    )

    # Health counts line: done: N | failed: N | running: N | queued: N | cancelled: N | total: N
    _HEALTH_COUNTS_RE = re.compile(
        r"done:\s*(\d+).*?failed:\s*(\d+).*?running:\s*(\d+).*?queued:\s*(\d+)"
        r".*?cancelled:\s*(\d+).*?total:\s*(\d+)",
    )

    # Daily breakdown line contains a date: YYYY-MM-DD  <detail>
    _DAY_LINE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(.*)")

    # Detail header: emoji *run 詳細: `run_id`*
    _DETAIL_HEADER_RE = re.compile(r"run 詳細: `([^`]+)`")

    # Detail kind / status: 種別: `kind`　状態: `status`  (　= U+3000 full-width space)
    _DETAIL_KIND_STATUS_RE = re.compile(r"種別:\s*`([^`]+)`\s+状態:\s*`([^`]+)`")

    # Detail queued_at: 受付: YYYY-MM-DD HH:MM:SS
    _DETAIL_QUEUED_RE = re.compile(r"受付:\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")

    # Emoji → status mapping for run list lines.
    _EMOJI_STATUS: dict[str, str] = {
        "\u2705": "done",           # ✅
        "\u274c": "failed",         # ❌
        "\u23f3": "queued",         # ⏳
        "\U0001f504": "running",    # 🔄
        "\u26d4": "cancelled",      # ⛔
    }

    # ── Constructor ────────────────────────────────────────────────────────────

    def __init__(self, *, gateway_url: str, gateway_token: str, timeout: int = 5) -> None:
        if _requests_lib is None:
            raise RuntimeError(
                "RunsAdapter requires the 'requests' package. "
                "Install it: pip install 'requests>=2.31'"
            )
        self._base = gateway_url.rstrip("/")
        self._headers: dict[str, str] = {"Authorization": f"Bearer {gateway_token}"}
        self._timeout = timeout

    # ── Internal HTTP call ─────────────────────────────────────────────────────

    def _call(self, text: str) -> str:
        """POST-equivalent fetch to GET /plugins/run-viewer/runs?text=<text>.

        Args:
            text: Arguments to pass after ``/runs`` (e.g. ``"last=5"``,
                ``"health 7d"``, ``"run_20260420_081200_abc"``).

        Returns:
            Raw display text produced by the run-viewer plugin.

        Raises:
            requests.HTTPError: On non-2xx gateway response.
            requests.Timeout: When the gateway does not respond within
                ``self._timeout`` seconds.
        """
        url = f"{self._base}/plugins/run-viewer/runs?text={urllib.parse.quote(text)}"
        resp = _requests_lib.get(url, headers=self._headers, timeout=self._timeout)
        resp.raise_for_status()
        data: dict = resp.json()
        return data.get("text", "")

    # ── Internal JSON call ─────────────────────────────────────────────────────

    def _call_json(self, **params: str) -> dict:
        """Call GET /plugins/run-viewer/runs?format=json&<params> and return parsed JSON.

        Args:
            **params: Query parameters merged with ``format=json``.

        Returns:
            Parsed JSON response dict.

        Raises:
            requests.HTTPError: On non-2xx gateway response.
            requests.Timeout: When the gateway does not respond within
                ``self._timeout`` seconds.
        """
        query = urllib.parse.urlencode({"format": "json", **params})
        url = f"{self._base}/plugins/run-viewer/runs?{query}"
        resp = _requests_lib.get(url, headers=self._headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    # ── Fetch methods ──────────────────────────────────────────────────────────

    def fetch_recent(self, limit: int = 10) -> str:
        """Fetch recent run records.

        Equivalent to ``/runs last=<limit>``.

        Args:
            limit: Maximum number of records to return (default 10, gateway
                hard-cap 50 without ``last=``; up to 1000 with ``last=``).

        Returns:
            Raw display text from the run-viewer plugin.
        """
        return self._call(f"last={limit}")

    def fetch_health(self, date_spec: str = "") -> str:
        """Fetch run health summary.

        Equivalent to ``/runs health [<date_spec>]``.

        Args:
            date_spec: One of ``""`` (today), ``"7d"`` (rolling window),
                ``"YYYY-MM-DD"`` (specific date), or
                ``"YYYY-MM-DD..YYYY-MM-DD"`` (inclusive range).

        Returns:
            Raw display text from the run-viewer plugin.
        """
        text = f"health {date_spec}".strip() if date_spec else "health"
        return self._call(text)

    def fetch_detail(self, run_id: str) -> str:
        """Fetch full detail for a single run record.

        Equivalent to ``/runs <run_id>``.

        Args:
            run_id: Run identifier (e.g. ``"run_20260420_081200_abc"``).

        Returns:
            Raw display text from the run-viewer plugin.
        """
        return self._call(run_id)

    def fetch_recent_json(
        self,
        limit: int = 50,
        status: str | None = None,
        date: str | None = None,
    ) -> list[dict]:
        """Fetch recent run records as full structured JSON.

        Calls ``GET /plugins/run-viewer/runs?format=json&mode=list`` and returns
        actual run record dicts — the same shape stored in the run JSON files.
        Prefer this over :meth:`fetch_recent` whenever the caller needs complete
        field coverage (``queued_at``, ``result``, ``error``, ``retry_of``, …).

        Args:
            limit: Maximum number of records (default 50, hard-capped at 1000 by
                the gateway).
            status: If set, only return records with this ``status`` value
                (``"done"`` / ``"failed"`` / ``"running"`` / ``"queued"`` /
                ``"cancelled"``).
            date: If set (``"YYYY-MM-DD"``), only scan that date directory.

        Returns:
            List of full run record dicts (may be empty).

        Raises:
            requests.HTTPError: On non-2xx gateway response.
            requests.Timeout: On gateway timeout.
        """
        params: dict[str, str] = {"mode": "list", "limit": str(limit)}
        if status:
            params["status"] = status
        if date:
            params["date"] = date
        data = self._call_json(**params)
        runs = data.get("runs")
        return runs if isinstance(runs, list) else []

    def fetch_detail_json(self, run_id: str) -> dict | None:
        """Fetch a single run record as full structured JSON.

        Calls ``GET /plugins/run-viewer/runs?format=json&mode=detail&run_id=ID``.
        Prefer this over :meth:`fetch_detail` whenever the caller needs complete
        field coverage.

        Args:
            run_id: Run identifier (e.g. ``"run_20260420_081200_abc"``).

        Returns:
            Full run record dict, or ``None`` when the run does not exist
            (gateway returns 404).

        Raises:
            requests.HTTPError: On non-2xx, non-404 gateway response.
            requests.Timeout: On gateway timeout.
        """
        try:
            data = self._call_json(mode="detail", run_id=run_id)
            run = data.get("run")
            return run if isinstance(run, dict) else None
        except _requests_lib.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    # ── Parse hooks ────────────────────────────────────────────────────────────

    @staticmethod
    def parse_recent(raw_text: str) -> dict:
        """Thin structured parse of ``/runs last=N`` output.

        Returns a dict with shape::

            {
                "runs": [
                    {
                        "run_id":  "run_20260420_081200_abc",
                        "kind":    "health",
                        "status":  "done",       # None if emoji unrecognised
                        "date":    "2026-04-20",
                        "time":    "08:12:00",
                        "elapsed": "1.2s",       # None if absent
                        "retry":   False,
                        "summary": "...",         # None if absent
                    },
                    ...
                ],
                "raw_text": "<original text>",
            }

        Unknown lines (header, footer, empty) are silently skipped.
        """
        runs: list[dict] = []
        for line in raw_text.splitlines():
            m = RunsAdapter._RUN_LINE_RE.search(line)
            if not m:
                continue
            run_id, kind, date, time_, elapsed, retry_marker, summary = m.groups()
            # Infer status from the leading emoji (first non-space char).
            status: str | None = None
            for emoji_char, s in RunsAdapter._EMOJI_STATUS.items():
                if line.lstrip().startswith(emoji_char):
                    status = s
                    break
            runs.append({
                "run_id": run_id,
                "kind": kind,
                "status": status,
                "date": date,
                "time": time_,
                "elapsed": elapsed,
                "retry": retry_marker is not None,
                "summary": summary.strip() if summary else None,
            })
        return {"runs": runs, "raw_text": raw_text}

    @staticmethod
    def parse_health(raw_text: str) -> dict:
        """Thin structured parse of ``/runs health [...]`` output.

        Returns a dict with shape::

            {
                "date":      "2026-04-20",               # or "2026-04-14..2026-04-20"
                "time_zone": "Asia/Tokyo",               # None when not present
                "status":    "ok" | "degraded" | "active" | None,
                "counts": {
                    "done": 6, "failed": 0, "running": 0,
                    "queued": 0, "cancelled": 0, "total": 6,
                },
                "daily": [          # populated only for multi-day ranges
                    {
                        "date": "2026-04-20",
                        "done": 0, "failed": 1, "running": 0,
                        "queued": 0, "cancelled": 0,
                        "no_runs": False,
                    },
                    ...
                ],
                "raw_text": "<original text>",
            }
        """
        result: dict = {
            "date": None,
            "time_zone": None,
            "status": None,
            "counts": {"done": 0, "failed": 0, "running": 0, "queued": 0, "cancelled": 0, "total": 0},
            "daily": [],
            "raw_text": raw_text,
        }
        in_breakdown = False

        for line in raw_text.splitlines():
            stripped = line.strip()

            # Header: emoji *run health (date-or-range)* [· TimeZone]
            hm = RunsAdapter._HEALTH_HEADER_RE.search(stripped)
            if hm:
                result["date"] = hm.group(1)
                tz = (hm.group(2) or "").strip()
                result["time_zone"] = tz if tz else None
                continue

            # Status line: "status: ok" / "status: degraded · failed=N"
            if stripped.startswith("status:"):
                sm = re.match(r"status:\s+(\w+)", stripped)
                if sm:
                    result["status"] = sm.group(1)
                continue

            # Counts line: "done: N | failed: N | ..."
            cm = RunsAdapter._HEALTH_COUNTS_RE.search(stripped)
            if cm:
                result["counts"] = {
                    "done": int(cm.group(1)),
                    "failed": int(cm.group(2)),
                    "running": int(cm.group(3)),
                    "queued": int(cm.group(4)),
                    "cancelled": int(cm.group(5)),
                    "total": int(cm.group(6)),
                }
                continue

            # Daily breakdown section marker
            if stripped == "*daily breakdown:*":
                in_breakdown = True
                continue

            # Daily breakdown entry: emoji YYYY-MM-DD  detail
            if in_breakdown and stripped:
                dm = RunsAdapter._DAY_LINE_RE.search(stripped)
                if dm:
                    day_date = dm.group(1)
                    day_detail = dm.group(2).strip()
                    entry: dict = {
                        "date": day_date,
                        "done": 0, "failed": 0, "running": 0,
                        "queued": 0, "cancelled": 0,
                        "no_runs": day_detail == "no runs",
                    }
                    for key in ("done", "failed", "running", "queued", "cancelled"):
                        km = re.search(rf"{key}=(\d+)", day_detail)
                        if km:
                            entry[key] = int(km.group(1))
                    result["daily"].append(entry)

        return result

    @staticmethod
    def parse_detail(raw_text: str) -> dict:
        """Thin structured parse of ``/runs <run_id>`` output.

        Returns a dict with shape::

            {
                "run_id":      "run_20260420_081200_abc" | None,
                "kind":        "health" | None,
                "status":      "done" | None,
                "queued_date": "2026-04-20" | None,
                "queued_time": "08:12:00" | None,
                "raw_text":    "<original text>",
            }

        Fields not found in the text are ``None``; ``raw_text`` always contains
        the full original string for fallback rendering.
        """
        result: dict = {
            "run_id": None,
            "kind": None,
            "status": None,
            "queued_date": None,
            "queued_time": None,
            "raw_text": raw_text,
        }
        for line in raw_text.splitlines():
            stripped = line.strip()

            # Header: emoji *run 詳細: `run_id`*
            hm = RunsAdapter._DETAIL_HEADER_RE.search(stripped)
            if hm:
                result["run_id"] = hm.group(1)
                continue

            # Kind + status: 種別: `kind`　状態: `status`
            ksm = RunsAdapter._DETAIL_KIND_STATUS_RE.search(stripped)
            if ksm:
                result["kind"] = ksm.group(1)
                result["status"] = ksm.group(2)
                continue

            # Queued-at: 受付: YYYY-MM-DD HH:MM:SS
            qm = RunsAdapter._DETAIL_QUEUED_RE.search(stripped)
            if qm:
                result["queued_date"] = qm.group(1)
                result["queued_time"] = qm.group(2)
                continue

        return result
