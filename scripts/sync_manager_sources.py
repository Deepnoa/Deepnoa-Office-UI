#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


BASE_URL = os.getenv("STAR_MANAGER_BASE_URL", "http://127.0.0.1:19000").rstrip("/")
HOME = Path(os.path.expanduser("~"))
GITHUB_WORKER_LOG = HOME / "bot" / "github_queue_local" / "log" / "worker.log"
GITHUB_DEPLOY_LOG = HOME / "bot" / "github_queue_local" / "log" / "deploy.log"
OPENCLAW_CRON_JOBS = HOME / ".openclaw" / "cron" / "jobs.json"
WINDOW_SECONDS = int(os.getenv("STAR_MANAGER_SYNC_WINDOW_SECONDS", "300"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def file_recent(path: Path, seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        age = now_utc().timestamp() - path.stat().st_mtime
        return age <= seconds
    except Exception:
        return False


def post_event(payload: dict) -> None:
    req = request.Request(
        f"{BASE_URL}/manager/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5):
        pass


def sync_github_dev() -> None:
    if file_recent(GITHUB_DEPLOY_LOG, WINDOW_SECONDS) or file_recent(GITHUB_WORKER_LOG, WINDOW_SECONDS):
        detail = "GitHub webhook queue is being processed and deployment flow is active."
        payload = {
            "source": "github",
            "event_type": "connector.status.changed",
            "state": "executing",
            "detail": detail,
        }
    else:
        payload = {
            "source": "github",
            "event_type": "connector.status.changed",
            "state": "idle",
            "detail": "GitHub webhook queue is idle.",
        }
    post_event(payload)


def sync_ops_cron() -> None:
    if not OPENCLAW_CRON_JOBS.exists():
        post_event({
            "source": "cron",
            "event_type": "cron_check",
            "state": "idle",
            "detail": "No Openclaw cron jobs file was found.",
        })
        return

    with OPENCLAW_CRON_JOBS.open("r", encoding="utf-8") as f:
        data = json.load(f)

    jobs = data.get("jobs") or []
    enabled = [job for job in jobs if job.get("enabled")]
    errors = [job for job in enabled if (job.get("state") or {}).get("lastStatus") == "error"]
    running = [job for job in enabled if (job.get("state") or {}).get("lastStatus") in {"running", "queued"}]

    if errors:
        state = "error"
        detail = f"{len(errors)} scheduled system checks currently need attention."
    elif running:
        state = "syncing"
        detail = f"{len(running)} scheduled system checks are currently active."
    else:
        state = "idle"
        detail = f"{len(enabled)} scheduled system checks are configured and standing by."

    post_event({
        "source": "cron",
        "event_type": "cron_check",
        "state": state,
        "detail": detail,
    })


def main() -> int:
    sync_github_dev()
    sync_ops_cron()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
