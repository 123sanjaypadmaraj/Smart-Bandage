"""
Runs ml/evaluation/scenario_backtest.py under pytest -- the "ML
experiments" bullet of Blueprint §10 Phase 6, scoped to a backtest against
simulated ground truth (see that module's docstring for why there's no
trained model here yet).
"""
from __future__ import annotations

import pytest

from ml.evaluation.scenario_backtest import EXPECTED_SIGNALS, run_backtest


@pytest.mark.parametrize("report", run_backtest(), ids=lambda r: r.scenario)
def test_scenario_matches_expected_signals(report):
    assert report.expected <= report.signals_seen, (
        f"{report.scenario}: expected {sorted(report.expected)} to be a subset of "
        f"observed {sorted(report.signals_seen)} over {report.ticks_run} ticks"
    )


def test_every_simulator_scenario_has_a_labelled_expectation():
    from simulator.scenarios.scenarios import list_scenarios

    assert set(list_scenarios()) == set(EXPECTED_SIGNALS)
