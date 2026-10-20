#!/usr/bin/env python3
"""Generate a real JWT_SECRET for a production deploy (backend/app/config.py:
Settings.validate() rejects the dev-only default and anything under 16
chars once ENVIRONMENT=production).

    python scripts/generate_secret.py            # 64-char URL-safe secret
    python scripts/generate_secret.py --length 96 # more entropy, still fine

Prints the secret to stdout and nothing else touches disk -- this script
never writes a .env file or any other file, on purpose: piping a secret
through a file this repo could accidentally `git add` defeats the point.
Feed the output straight to wherever `JWT_SECRET` actually needs to live:

    export JWT_SECRET=$(python scripts/generate_secret.py)

then hand it to your platform's secret store (a cloud provider's secrets
manager, `docker secret create`, a CI provider's encrypted secrets, a
Kubernetes Secret, ...) -- never a checked-in .env. See the "Production
readiness" section of README.md.
"""
from __future__ import annotations

import argparse
import secrets


def generate_secret(length: int) -> str:
    """`length` random bytes, URL-safe-base64-encoded -- longer than the
    16-char floor Settings.validate() enforces by a wide margin so this is
    never the thing that trips it up."""
    if length < 16:
        raise ValueError("length must be at least 16 bytes for a real production secret")
    return secrets.token_urlsafe(length)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--length",
        type=int,
        default=48,
        help="random bytes of entropy before base64url encoding (default: 48, ~64 chars out)",
    )
    args = parser.parse_args()
    print(generate_secret(args.length))


if __name__ == "__main__":
    main()
