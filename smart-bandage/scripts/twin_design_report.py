"""
Characterize and compare bandage designs on the digital twin's virtual bench.

    python scripts/twin_design_report.py                     # all built-in presets
    python scripts/twin_design_report.py --preset standard
    python scripts/twin_design_report.py --design my_design.json --preset standard
    python scripts/twin_design_report.py --export standard > standard.json

A design file is the JSON that `BandageDesign.to_dict()` / `--export` writes;
edit a number, rerun, and compare against a preset. See
docs/architecture/bandage_twin.md for what the columns mean and -- importantly --
that the design parameters are illustrative, not fitted to a measured device.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from digital_twin.bandage import PRESETS, BandageDesign, compare, format_table  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", action="append", choices=sorted(PRESETS), help="built-in design (repeatable)")
    parser.add_argument("--design", action="append", metavar="FILE", help="design JSON file (repeatable)")
    parser.add_argument("--export", choices=sorted(PRESETS), help="print a preset as JSON and exit")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if args.export:
        print(json.dumps(PRESETS[args.export]().to_dict(), indent=2))
        return 0

    designs = [PRESETS[name]() for name in (args.preset or [])]
    for path in args.design or []:
        with open(path, encoding="utf-8") as fh:
            designs.append(BandageDesign.from_dict(json.load(fh)))
    if not designs:
        designs = [make() for make in PRESETS.values()]

    print(format_table(compare(designs, seed=args.seed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
