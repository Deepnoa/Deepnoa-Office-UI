#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from urllib import request


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a manager event to Star-Office-UI")
    parser.add_argument("--base-url", default="http://127.0.0.1:19000")
    parser.add_argument("--source", required=True)
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--state", default="idle")
    parser.add_argument("--detail", default="")
    parser.add_argument("--role")
    parser.add_argument("--task-id")
    parser.add_argument("--provenance", default="actual")
    parser.add_argument("--approval-status", default="")
    args = parser.parse_args()

    payload = {
        "source": args.source,
        "event_type": args.event_type,
        "state": args.state,
        "detail": args.detail,
        "provenance": args.provenance,
    }
    if args.role:
        payload["role"] = args.role
    if args.task_id:
        payload["task_id"] = args.task_id
    if args.approval_status:
        payload["approval_status"] = args.approval_status
    payload.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))

    req = request.Request(
        f"{args.base_url.rstrip('/')}/manager/event",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as resp:
        sys.stdout.write(resp.read().decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
