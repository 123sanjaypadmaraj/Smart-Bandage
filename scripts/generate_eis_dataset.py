"""Regenerate datasets/ecoli_eis/ (deterministic): python scripts/generate_eis_dataset.py [--seed N] [--out DIR]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.bandage.eis_dataset import DEFAULT_DIR, Spec, generate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=Spec.seed)
    ap.add_argument("--noise-pct", type=float, default=Spec.noise_pct)
    ap.add_argument("--out", type=Path, default=DEFAULT_DIR)
    a = ap.parse_args()
    runs, rows = generate(a.out, Spec(seed=a.seed, noise_pct=a.noise_pct))
    print(f"wrote {runs} runs, {rows} spectrum rows to {a.out}")


if __name__ == "__main__":
    main()
