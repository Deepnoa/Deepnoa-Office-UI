#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
from urllib import request


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openclaw_payload_mapper import extract_approval_events


def post_manager_event(base_url: str, payload: dict) -> None:
    req = request.Request(
        f"{base_url.rstrip('/')}/manager/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5):
        pass


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def build_task_id(agent: str, source: str, message: str) -> str:
    stamp = iso_now()
    digest = hashlib.sha1(f"{agent}::{source}::{message}::{stamp}".encode("utf-8")).hexdigest()[:12]
    return f"task_{digest}"


def run_agent(agent: str, message: str, timeout: int) -> subprocess.CompletedProcess[str]:
    root = os.environ.get("OPENCLAW_ROOT", os.path.expanduser("~/openclaw"))
    cmd = [
        "node",
        os.path.join(root, "dist", "index.js"),
        "agent",
        "--agent",
        agent,
        "--message",
        message,
        "--json",
        "--timeout",
        str(timeout),
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def is_success(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return False
    if payload.get("status") != "ok":
        return False
    meta = ((payload.get("result") or {}).get("meta") or {})
    if meta.get("stopReason") == "error":
        return False
    text_blob = json.dumps((payload.get("result") or {}).get("payloads") or [], ensure_ascii=False)
    if "timed out" in text_blob.lower():
        return False
    return True


def parse_result_payload(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(result.stdout or "{}")
    except Exception:
        return {}


def emit_task_lifecycle(base_url: str, *, source: str, agent: str, task_id: str, detail: str) -> None:
    timestamp = iso_now()
    for event_type in ("task.created", "task.assigned", "task.started"):
        post_manager_event(base_url, {
            "role": agent,
            "source": source,
            "event_type": event_type,
            "state": "executing",
            "detail": detail,
            "task_id": task_id,
            "provenance": "actual",
            "timestamp": timestamp,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a specific Openclaw role agent with manager updates.")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--fallback-agent", default="main")
    parser.add_argument("--message", required=True)
    parser.add_argument("--base-url", default=os.getenv("STAR_MANAGER_BASE_URL", "http://127.0.0.1:19000"))
    parser.add_argument("--source", default="manager")
    parser.add_argument("--event-type", default="manual")
    parser.add_argument("--state", default="executing")
    parser.add_argument("--start-detail", required=True)
    parser.add_argument("--success-detail", required=True)
    parser.add_argument("--failure-detail", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    task_id = build_task_id(args.agent, args.source, args.message)
    started_at = time.time()
    emit_task_lifecycle(args.base_url, source=args.source, agent=args.agent, task_id=task_id, detail=args.start_detail)
    post_manager_event(args.base_url, {
        "role": args.agent,
        "source": args.source,
        "event_type": args.event_type,
        "state": args.state,
        "detail": args.start_detail,
        "task_id": task_id,
        "provenance": "actual",
        "timestamp": iso_now(),
    })

    primary = run_agent(args.agent, args.message, args.timeout)
    primary_payload = parse_result_payload(primary)
    for approval_event in extract_approval_events(
        primary_payload,
        source=args.source,
        agent=args.agent,
        task_id=task_id,
        started_at=started_at,
    ):
        post_manager_event(args.base_url, approval_event)
    if is_success(primary):
        post_manager_event(args.base_url, {
            "role": args.agent,
            "source": args.source,
            "event_type": "task.completed",
            "state": "idle",
            "detail": args.success_detail,
            "task_id": task_id,
            "provenance": "actual",
            "timestamp": iso_now(),
        })
        sys.stdout.write(primary.stdout)
        return 0

    if args.fallback_agent and args.fallback_agent != args.agent:
        fallback = run_agent(args.fallback_agent, args.message, args.timeout)
        fallback_payload = parse_result_payload(fallback)
        for approval_event in extract_approval_events(
            fallback_payload,
            source="fallback",
            agent=args.fallback_agent,
            task_id=task_id,
            started_at=started_at,
        ):
            post_manager_event(args.base_url, approval_event)
        if is_success(fallback):
            post_manager_event(args.base_url, {
                "role": args.fallback_agent,
                "source": "fallback",
                "event_type": "task.completed",
                "state": "idle",
                "detail": args.success_detail,
                "task_id": task_id,
                "provenance": "actual",
                "timestamp": iso_now(),
            })
            sys.stdout.write(fallback.stdout)
            return 0

    post_manager_event(args.base_url, {
        "role": args.agent,
        "source": args.source,
        "event_type": "task.failed",
        "state": "error",
        "detail": args.failure_detail,
        "task_id": task_id,
        "provenance": "actual",
        "timestamp": iso_now(),
    })
    if primary.stdout:
        sys.stdout.write(primary.stdout)
    if primary.stderr:
        sys.stderr.write(primary.stderr)
    return primary.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
