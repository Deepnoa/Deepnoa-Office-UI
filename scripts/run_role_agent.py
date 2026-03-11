#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from urllib import request


def post_manager_event(base_url: str, payload: dict) -> None:
    req = request.Request(
        f"{base_url.rstrip('/')}/manager/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5):
        pass


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

    post_manager_event(args.base_url, {
        "role": args.agent,
        "source": args.source,
        "event_type": args.event_type,
        "state": args.state,
        "detail": args.start_detail,
    })

    primary = run_agent(args.agent, args.message, args.timeout)
    if is_success(primary):
        post_manager_event(args.base_url, {
            "role": args.agent,
            "source": args.source,
            "event_type": args.event_type,
            "state": "idle",
            "detail": args.success_detail,
        })
        sys.stdout.write(primary.stdout)
        return 0

    if args.fallback_agent and args.fallback_agent != args.agent:
        fallback = run_agent(args.fallback_agent, args.message, args.timeout)
        if is_success(fallback):
            post_manager_event(args.base_url, {
                "role": args.fallback_agent,
                "source": "fallback",
                "event_type": args.event_type,
                "state": "idle",
                "detail": args.success_detail,
            })
            sys.stdout.write(fallback.stdout)
            return 0

    post_manager_event(args.base_url, {
        "role": args.agent,
        "source": args.source,
        "event_type": args.event_type,
        "state": "error",
        "detail": args.failure_detail,
    })
    if primary.stdout:
        sys.stdout.write(primary.stdout)
    if primary.stderr:
        sys.stderr.write(primary.stderr)
    return primary.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
