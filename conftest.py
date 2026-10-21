import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Keep tests hermetic: load_dotenv() never overrides an already-set variable, so
# pinning an empty key here stops a developer's real .env GEMINI_API_KEY from
# leaking into /health and the Gemini client tests.
os.environ["GEMINI_API_KEY"] = ""

from hypothesis import settings

# Two Hypothesis profiles, chosen by the HYPOTHESIS_PROFILE env var (unset
# locally -> "default": fewer examples, keeps a bare `pytest` fast; CI and
# scripts/run_checks.sh both export HYPOTHESIS_PROFILE=ci -> deeper search
# before every push. Both disable the per-example deadline: this repo's own
# dev machines are frequently shared by several concurrent sessions too, so
# the same CPU-contention timing noise that would flake a shared CI runner
# shows up locally (e.g. test_engine_different_seeds_diverge tripping
# DeadlineExceeded on a slow first example, then passing clean on retry).
settings.register_profile("default", max_examples=50, deadline=None)
settings.register_profile("ci", max_examples=500, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
