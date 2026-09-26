import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from hypothesis import settings

# Two Hypothesis profiles, chosen by the HYPOTHESIS_PROFILE env var (unset
# locally -> "default": fewer examples, keeps a bare `pytest` fast; CI and
# scripts/run_checks.sh both export HYPOTHESIS_PROFILE=ci -> deeper search
# before every push, and no per-example deadline so a slower/shared CI
# runner can't turn a correct property into a flaky failure).
settings.register_profile("default", max_examples=50)
settings.register_profile("ci", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
