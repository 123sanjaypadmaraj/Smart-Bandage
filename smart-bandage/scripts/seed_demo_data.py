#!/usr/bin/env python3
"""Register a couple of demo devices and kick off a running simulation on
one of them, against an already-running backend (dev server or Docker).

Uses only the stdlib (urllib) so it needs no extra deps beyond the backend
itself being reachable -- see `scripts/dev_up.sh` / `docker compose up`.

    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --api-base http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEMO_DEVICES = [
    {"device_id": "SB-DEMO-01", "name": "Demo Bandage — Forearm", "firmware_version": "0.1.0", "channels": ["CH-01"]},
    {"device_id": "SB-DEMO-02", "name": "Demo Bandage — Shin", "firmware_version": "0.1.0", "channels": ["CH-01"]},
]


def call(api_base: str, method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{api_base}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--no-simulate", action="store_true", help="register devices but don't start a simulation")
    args = parser.parse_args()

    print(f"Logging in to {args.api_base} as {args.username}...")
    login = call(args.api_base, "POST", "/auth/login", body={"username": args.username, "password": args.password})
    token = login["access_token"]

    for device in DEMO_DEVICES:
        print(f"Registering {device['device_id']}...")
        call(args.api_base, "POST", "/devices/register", token=token, body=device)

    if not args.no_simulate:
        target = DEMO_DEVICES[0]["device_id"]
        print(f"Starting a 'rising_concentration' simulation on {target}...")
        call(
            args.api_base,
            "POST",
            "/simulation/start",
            token=token,
            body={"device_id": target, "channels": ["CH-01"], "scenario": "rising_concentration"},
        )

    print("Done. Sign in to the dashboard to see the demo devices.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        raise
