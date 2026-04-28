from __future__ import annotations

import json
import subprocess
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIR = Path("/home/deepnoa/deepnoa-agent-runtime/runs")
MAX_REPLY_DRAFTS = 50

_PROJECT_ROOT = Path(__file__).parent.parent.parent
DRAFT_ACTIONS_FILE = _PROJECT_ROOT / "draft-actions.json"
_draft_lock = threading.Lock()

VALID_ACTIONS: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "regenerate": "pending_regenerate",
}

# OpenClaw / deepnoa-agent-runtime paths
_QUEUE_PATH = Path("/home/deepnoa/openclaw/runs/queued-runtime-tasks.jsonl")
_RUNTIME_DIR = Path("/home/deepnoa/deepnoa-agent-runtime")
_PYTHON_BIN = _RUNTIME_DIR / ".venv/bin/python"
_RUN_SCRIPT = _RUNTIME_DIR / "scripts/run_agent.py"


def load_draft_actions() -> dict[str, dict[str, str]]:
    """Load persisted draft action states. Returns {} on missing file or parse error."""
    try:
        if DRAFT_ACTIONS_FILE.exists():
            data = json.loads(DRAFT_ACTIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_draft_action(task_id: str, status: str) -> None:
    """Persist an action state for task_id, replacing any previous value."""
    with _draft_lock:
        actions = load_draft_actions()
        actions[task_id] = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        DRAFT_ACTIONS_FILE.write_text(
            json.dumps(actions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _safe_iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except Exception:
        return ""


def _normalize_draft(data: dict[str, Any], result_path: Path) -> dict[str, Any]:
    result_payload = data.get("result") if isinstance(data.get("result"), dict) else {}
    created_at = (
        str(data.get("ended_at") or "").strip()
        or str(data.get("started_at") or "").strip()
        or _safe_iso_from_mtime(result_path)
    )
    return {
        "task_id": str(data.get("task_id") or "").strip(),
        "role": str(data.get("role") or "").strip(),
        "status": str(data.get("status") or "").strip(),
        "exit_code": data.get("exit_code"),
        "created_at": created_at,
        "content": str(result_payload.get("content") or "").strip(),
    }


def load_reply_drafts(limit: int = MAX_REPLY_DRAFTS) -> list[dict[str, Any]]:
    """Load recent reply drafts, overlaying persisted action states."""
    if not RUNS_DIR.exists():
        return []

    drafts: list[dict[str, Any]] = []
    for result_path in RUNS_DIR.glob("*/result.json"):
        try:
            raw = result_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        result_payload = data.get("result")
        if not isinstance(result_payload, dict):
            continue
        if str(result_payload.get("type") or "").strip() != "reply_draft":
            continue
        normalized = _normalize_draft(data, result_path)
        if not normalized["content"]:
            continue
        drafts.append(normalized)

    drafts.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if limit > 0:
        drafts = drafts[:limit]

    # Overlay persisted action states so UI-driven status survives page reloads
    actions = load_draft_actions()
    for draft in drafts:
        tid = draft.get("task_id", "")
        if tid and tid in actions:
            draft["status"] = actions[tid]["status"]

    return drafts


def _load_queue_entry(task_id: str) -> dict[str, Any] | None:
    """Return the latest queue entry for task_id from queued-runtime-tasks.jsonl."""
    if not _QUEUE_PATH.exists():
        return None
    latest: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for raw_line in _QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        tid = str(item.get("task_id") or "").strip()
        if not tid:
            continue
        latest[tid] = item
    return latest.get(task_id)


def _run_regenerate_bg(task_id: str, role: str, payload: dict[str, Any]) -> None:
    """Background thread: invoke run_agent.py, then update draft status."""
    try:
        tmp_dir = Path("/tmp/openclaw")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        input_path = tmp_dir / f"task-{task_id}.json"
        output_path = _RUNTIME_DIR / "runs" / task_id / "result.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        cmd = [
            str(_PYTHON_BIN),
            str(_RUN_SCRIPT),
            "--role", role,
            "--task-id", task_id,
            "--input", str(input_path),
            "--output", str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            save_draft_action(task_id, "regenerated")
        else:
            save_draft_action(task_id, "regenerate_failed")
    except Exception:
        save_draft_action(task_id, "regenerate_failed")


def trigger_regenerate(task_id: str) -> tuple[bool, str]:
    """
    Start a background regeneration for task_id.
    Returns (ok, message). Sets status to pending_regenerate immediately,
    then to regenerated or regenerate_failed when the background job completes.
    """
    entry = _load_queue_entry(task_id)
    if entry is None:
        return False, "キューエントリが見つかりません"

    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return False, "ペイロードが無効です"

    role = str(entry.get("role") or "dev").strip() or "dev"

    save_draft_action(task_id, "pending_regenerate")
    t = threading.Thread(target=_run_regenerate_bg, args=(task_id, role, payload), daemon=True)
    t.start()
    return True, "再生成を開始しました"
