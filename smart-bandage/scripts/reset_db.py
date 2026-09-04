#!/usr/bin/env python3
"""Wipe the local dev SQLite database so the next `uvicorn`/`pytest` run
starts clean (a fresh `smart_bandage.db` is recreated on next backend
startup -- see backend/app/database.py::init_db).

    python scripts/reset_db.py            # asks for confirmation
    python scripts/reset_db.py --yes      # skip the prompt

Only touches the default SQLite file; does nothing if DATABASE_URL points
at Postgres (delete that database yourself, this script won't touch it).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "sqlite:///./smart_bandage.db")
    if not database_url.startswith("sqlite"):
        print(f"DATABASE_URL={database_url!r} isn't SQLite -- nothing to do here.", file=sys.stderr)
        return

    db_path = REPO_ROOT / "smart_bandage.db"
    if not db_path.exists():
        print(f"{db_path} doesn't exist -- nothing to reset.")
        return

    if not args.yes:
        reply = input(f"Delete {db_path}? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return

    db_path.unlink()
    print(f"Deleted {db_path}.")


if __name__ == "__main__":
    main()
