"""Tests for digital_twin/bandage: the bandage-design digital twin and its engine kernel."""
import copy
import json
import math
import random
from dataclasses import replace

import pytest

from common.interfaces.sensor_interface import SensorInterface
from digital_twin.bandage import (
    AdhesiveSpec,
    AptamerSpec,
    BandageDesign,
    BandageTwin,
    ElectrodeSpec,
    EnzymaticSpec,
    LayerSpec,
    PotentiometricSpec,
    PowerSpec,
    RngStreams,
    WoundBedParams,
    characterize,
    compare,
    fast_response_design,
    format_table,
    low_power_design,
    sensors_for,
    standard_design,
)
from digital_twin.bandage.kernel import Event, EventQueue, SimClock, derive_seed, ou_exact
from digital_twin.bandage.transducer import AptamerTransducer
from digital_twin.bandage.transport import DiffusionStack
from digital_twin.bandage.wound_bed import WoundBed
from simulator.faults.faults import CommsTimeoutError, SensorDisconnectedError

H = 3600.0
NO_NOISE = WoundBedParams(process_noise=0.0)


# ------------------------------------------------------------------ kernel
def test_derive_seed_is_stable_and_path_sensitive():
    assert derive_seed(1, "a", "b") == derive_seed(1, "a", "b")
    assert derive_seed(1, "a", "b") != derive_seed(1, "a", "c")
    assert derive_seed(1, "a") != derive_seed(2, "a")


def test_streams_are_isolated_from_each_other():
    """Drawing from one stream must not shift another -- the property that
    lets components be added or removed without changing anyone else's noise."""
    a = RngStreams(5)
    only_x = [a.get("x").random() for _ in range(5)]
    b = RngStreams(5)
    for _ in range(50):
        b.get("y").random()
    with_y = [b.get("x").random() for _ in range(5)]
    assert only_x == with_y


def test_streams_state_survives_json_roundtrip():
    s = RngStreams(9)
    s.get("x").random()
    s.get("x").gauss(0, 1)
    packed = json.loads(json.dumps(s.get_state()))
    expected = [s.get("x").random() for _ in range(3)]
    t = RngStreams(9)
    t.set_state(packed)
    assert [t.get("x").random() for _ in range(3)] == expected


def test_ou_exact_reaches_stationary_std_for_any_step():
    rng = random.Random(0)
    draws = [ou_exact(0.0, 2.0, 100.0, 0.5, 1e6, rng) for _ in range(4000)]
    mean = sum(draws) / len(draws)
    std = math.sqrt(sum((d - mean) ** 2 for d in draws) / len(draws))
    assert abs(mean - 2.0) < 0.05
    assert abs(std - 0.5) < 0.05


def test_ou_exact_zero_dt_is_identity():
    assert ou_exact(1.23, 0.0, 10.0, 1.0, 0.0, random.Random(0)) == 1.23


def test_clock_cannot_run_backwards():
    with pytest.raises(ValueError):
        SimClock().advance(-1)


def test_event_queue_orders_by_time_and_keeps_tie_order():
    q = EventQueue()
    q.schedule(Event(10, "b"))
    q.schedule(Event(5, "a"))
    q.schedule(Event(10, "c"))
    assert q.next_time() == 5
    assert [e.kind for e in q.pop_due(10)] == ["a", "b", "c"]
    assert len(q) == 0


# ------------------------------------------------------------------ design
def test_default_designs_validate():
    for make in (standard_design, low_power_design, fast_response_design):
        make()


def test_design_json_roundtrip_preserves_digest():
    d = standard_design()
    again = BandageDesign.from_dict(json.loads(json.dumps(d.to_dict())))
    assert again == d
    assert again.digest() == d.digest()


def test_digest_changes_when_any_number_changes():
    d = standard_design()
    thicker = d.with_changes(layers=(replace(d.layers[0], thickness_um=90.0), d.layers[1]))
    assert thicker.digest() != d.digest()


def test_design_rejects_bad_inputs():
    d = standard_design()
    with pytest.raises(ValueError, match="duplicate"):
        d.with_changes(electrodes=(d.electrodes[0], d.electrodes[0]))
    with pytest.raises(ValueError, match="transducer"):
        ElectrodeSpec("CH-09", "glucose", "glucose", AptamerSpec())
    with pytest.raises(ValueError, match="wet diffusivity"):
        LayerSpec("x", 10.0, 50.0, 10.0)
    with pytest.raises(ValueError, match="fit inside"):
        d.with_changes(sample_interval_s=2.0)
    with pytest.raises(ValueError):
        AdhesiveSpec(adhesion=1.5)


# --------------------------------------------------------------- transport
def _single_layer(thickness=100.0, d=100.0):
    return [LayerSpec("x", thickness, d, d)]


def test_transport_converges_to_wound_concentration():
    stack = DiffusionStack(_single_layer(), None, 1.0, initial_conc=0.0)
    for _ in range(200):
        stack.step(5.0, 10.0, [], 0.0)
    assert stack.surface == pytest.approx(10.0, rel=1e-3)


@pytest.mark.parametrize("dt", [0.1, 10.0, 1e3, 1e6])
def test_transport_is_stable_and_bounded_for_any_dt(dt):
    """Backward Euler: never overshoots the wound concentration or goes negative."""
    stack = DiffusionStack(_single_layer(), None, 1.0, initial_conc=0.0)
    previous = 0.0
    for _ in range(20):
        c = stack.step(dt, 10.0, [], 0.0)
        assert previous - 1e-9 <= c <= 10.0 + 1e-9
        previous = c


def test_transport_decay_rate_matches_analytic_first_mode():
    """One layer, fixed wound end, no-flux electrode end: the slowest mode
    decays at pi^2 * D / (4 L^2)."""
    L, D = 100.0, 100.0
    stack = DiffusionStack(_single_layer(L, D), None, 1.0, initial_conc=0.0)
    dt = 0.5
    for _ in range(int(300 / dt)):
        stack.step(dt, 1.0, [], 0.0)
    r1 = 1.0 - stack.surface
    for _ in range(int(100 / dt)):
        stack.step(dt, 1.0, [], 0.0)
    r2 = 1.0 - stack.surface
    measured = math.log(r1 / r2) / 100.0
    assert measured == pytest.approx(math.pi ** 2 * D / (4 * L ** 2), rel=0.05)


def _time_to_fraction(stack, fraction, conc=1.0, hydration=(), fouling=0.0, dt=1.0, limit=20000):
    t = 0.0
    while t < limit:
        t += dt
        if stack.step(dt, conc, hydration, fouling) >= fraction * conc:
            return t
    return math.inf


def test_thicker_stack_is_slower_and_fouling_slows_it_further():
    thin = _time_to_fraction(DiffusionStack(_single_layer(50), None, 1.0, 0.0), 0.9)
    thick = _time_to_fraction(DiffusionStack(_single_layer(150), None, 1.0, 0.0), 0.9)
    assert thick > 4 * thin  # ~ L^2

    membrane = LayerSpec("m", 30.0, 40.0, 40.0)
    clean = _time_to_fraction(DiffusionStack(_single_layer(), membrane, 1.0, 0.0), 0.9, fouling=0.0)
    fouled = _time_to_fraction(DiffusionStack(_single_layer(), membrane, 1.0, 0.0), 0.9, fouling=0.9)
    assert fouled > clean


def test_dry_pad_is_slower_than_wet_pad():
    layers = [LayerSpec("pad", 100.0, 10.0, 200.0, hydration_tau_s=100.0)]
    dry = _time_to_fraction(DiffusionStack(layers, None, 1.0, 0.0), 0.9, hydration=[0.0])
    wet = _time_to_fraction(DiffusionStack(layers, None, 1.0, 0.0), 0.9, hydration=[1.0])
    assert dry > 5 * wet


# -------------------------------------------------------------- transducer
def test_aptamer_reaches_equilibrium_occupancy():
    t = AptamerTransducer(AptamerSpec(kd_nM=100.0), 1.0, initial_conc_nM=0.0)
    t.step(1e5, 100.0, 37.0)
    assert t.occupancy == pytest.approx(0.5, rel=1e-6)


def test_aptamer_one_big_step_equals_many_small_ones():
    a = AptamerTransducer(AptamerSpec(), 1.0, 0.0)
    b = AptamerTransducer(AptamerSpec(), 1.0, 0.0)
    a.step(1000.0, 50.0, 37.0)
    for _ in range(1000):
        b.step(1.0, 50.0, 37.0)
    assert a.occupancy == pytest.approx(b.occupancy, rel=1e-9)
    assert a.probe_fraction == pytest.approx(b.probe_fraction, rel=1e-9)


def test_probe_loss_matches_the_spec_per_day():
    t = AptamerTransducer(AptamerSpec(desorption_per_day=0.03), 1.0, 0.0)
    t.step(86400.0, 0.0, 37.0)
    assert t.probe_fraction == pytest.approx(0.97, rel=1e-9)


# ------------------------------------------------------------- wound bed
def _bed(params=WoundBedParams(), seed=1):
    return WoundBed(params, random.Random(seed))


def _run_bed(bed, hours, dt=60.0, t0=0.0):
    t = t0
    while t < t0 + hours * H - 1e-9:
        bed.step(t, dt)
        t += dt
    return t


def test_healthy_wound_stays_clean():
    bed = _bed()
    _run_bed(bed, 72)
    assert bed.bacterial_load < 0.05


def test_infection_raises_load_and_shifts_every_analyte():
    bed = _bed()
    before = bed.analytes()
    bed.apply_event("infection", {"severity": 0.8, "duration_h": 100}, 0.0)
    _run_bed(bed, 60)
    after = bed.analytes()
    assert bed.bacterial_load > 0.3
    assert after.pathogen_marker > before.pathogen_marker + 100
    assert after.glucose < before.glucose
    assert after.pH > before.pH
    assert after.temperature_C > before.temperature_C


def test_antibiotic_and_debridement_reduce_load():
    bed = _bed()
    bed.apply_event("infection", {"severity": 0.8, "duration_h": 200}, 0.0)
    t = _run_bed(bed, 60)
    peak = bed.bacterial_load
    bed.apply_event("antibiotic", {"potency": 10.0, "duration_h": 100}, t)
    _run_bed(bed, 24, t0=t)
    assert bed.bacterial_load < 0.5 * peak

    bed2 = _bed()
    bed2.bacterial_load = 0.6
    bed2.apply_event("debridement", {"residual": 0.1}, 0.0)
    assert bed2.bacterial_load == pytest.approx(0.06)


def test_wound_ode_integration_is_step_size_insensitive():
    """RK4: 10 s steps and 10 min steps agree on the deterministic B/I state."""
    results = []
    for dt in (10.0, 600.0):
        bed = _bed(seed=3)
        bed.apply_event("infection", {"severity": 0.8, "duration_h": 100}, 0.0)
        _run_bed(bed, 30, dt=dt)
        results.append((bed.bacterial_load, bed.inflammation))
    assert results[0][0] == pytest.approx(results[1][0], rel=1e-3)
    assert results[0][1] == pytest.approx(results[1][1], rel=1e-3)


# -------------------------------------------------------------------- twin
def _readings(frames, cid="CH-01"):
    return [f.readings[cid].raw_signal for f in frames]


def test_same_seed_reproduces_exactly_and_different_seed_differs():
    def run(seed):
        tw = BandageTwin(seed=seed)
        tw.schedule(2 * H, "infection", severity=0.7)
        return _readings(tw.run(6 * H))

    assert run(11) == run(11)
    assert run(11) != run(12)


def test_unseeded_run_records_its_seed_so_it_can_be_replayed():
    a = BandageTwin()
    b = BandageTwin(seed=a.seed)
    assert _readings(a.run(H)) == _readings(b.run(H))


def test_reading_pattern_does_not_change_physics():
    """Advancing 1x600 s, 60x10 s or 600x1 s must land on the same true state."""
    def truth_after(chunks, chunk_s):
        tw = BandageTwin(seed=4, wound=NO_NOISE)
        tw.schedule(100.0, "infection", severity=0.9, duration_h=10)
        for _ in range(chunks):
            tw.advance(chunk_s)
        return tw.truth()

    a, b, c = truth_after(1, 600.0), truth_after(60, 10.0), truth_after(600, 1.0)
    for other in (b, c):
        assert other.bacterial_load == pytest.approx(a.bacterial_load, rel=1e-3)
        assert other.channels["CH-01"].local_concentration == pytest.approx(
            a.channels["CH-01"].local_concentration, rel=0.03
        )


def test_events_fire_at_their_exact_time():
    tw = BandageTwin(seed=1, design=standard_design().with_changes(adhesive=AdhesiveSpec(0.0, 900.0)))
    tw.schedule(95.0, "disturbance", severity=0.6)
    tw.advance(94.9)
    assert tw.truth().contact == pytest.approx(1.0)
    tw.advance(0.1)
    assert tw.t_s == pytest.approx(95.0)
    assert tw.truth().contact == pytest.approx(0.4)


def test_event_inside_a_long_advance_is_applied_mid_step_not_at_its_end():
    """One advance(1000) with an event at t=100 must let the effect play out
    for the remaining 900 s (contact re-sealing), not apply it at t=1000."""
    d = standard_design().with_changes(adhesive=AdhesiveSpec(0.0, 900.0))
    tw = BandageTwin(d, seed=1)
    tw.schedule(100.0, "disturbance", severity=0.6)
    tw.advance(1000.0)
    expected = 1.0 - 0.6 * math.exp(-900.0 / 900.0)
    assert tw.truth().contact == pytest.approx(expected, abs=1e-6)


def test_scheduling_validates():
    tw = BandageTwin(seed=1)
    with pytest.raises(ValueError, match="unknown event kind"):
        tw.schedule(10.0, "meteor")
    with pytest.raises(ValueError, match="channel_id"):
        tw.schedule(10.0, "electrode_fault", channel_id="NOPE")
    tw.advance(100.0)
    with pytest.raises(ValueError, match="past"):
        tw.schedule(50.0, "infection")


def test_infection_shows_up_in_the_electrodes_after_a_transport_lag():
    tw = BandageTwin(seed=2, wound=NO_NOISE, max_substep_s=30.0)
    tw.schedule(H, "infection", severity=0.9, duration_h=200)
    frames = tw.run(40 * H)
    ch1 = [f.truth.channels["CH-01"] for f in frames]
    baseline = ch1[10].ideal_au
    assert max(c.ideal_au for c in ch1) > baseline + 15
    # the electrode sees the marker later than the wound produces it
    lagging = [c for c in ch1 if c.local_concentration < 0.9 * c.wound_concentration]
    assert lagging
    # and every channel moves in the direction the wound biochemistry says
    g = [f.truth.channels["CH-02"].ideal_au for f in frames]
    p = [f.truth.channels["CH-03"].ideal_au for f in frames]
    assert g[-1] < g[10] - 10  # glucose consumed
    assert p[-1] > p[10] + 3  # alkaline shift


def test_measurement_noise_matches_the_designed_noise_floor():
    tw = BandageTwin(seed=5)
    tw.hold_bench(pathogen_marker=50.0)
    tw.advance(H)
    vals = []
    for _ in range(400):
        tw.advance(30.0)
        vals.append(tw.read_channel("CH-01").raw_signal)
    mean = sum(vals) / len(vals)
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
    expected = characterize(standard_design()).channels[0].noise_au_rms
    assert 0.5 * expected < std < 1.8 * expected


def test_bench_reading_matches_the_binding_equation():
    tw = BandageTwin(seed=1)
    tw.hold_bench(pathogen_marker=100.0)  # Kd = 100 nM -> occupancy 0.5
    tw.advance(H)
    ideal = tw.read_channel("CH-01", ideal=True).raw_signal
    assert ideal == pytest.approx(100.0 * (1 + 0.9 * 0.5), rel=0.02)


def test_battery_drain_matches_the_duty_cycle_estimate():
    tw = BandageTwin(seed=3)
    tw.advance(24 * H)
    used = 100.0 - tw.power.percent
    predicted = 100.0 * tw.power.average_current_mA(0.92) * 24 / tw.design.power.capacity_mAh
    assert used == pytest.approx(predicted, rel=0.08)


def test_depleted_battery_takes_the_device_offline():
    d = standard_design().with_changes(power=PowerSpec(capacity_mAh=0.5))
    tw = BandageTwin(d, seed=1)
    frames = tw.run(6 * H)
    assert all(isinstance(r, Exception) for r in frames[-1].readings.values())
    assert "battery" in str(frames[-1].readings["CH-01"])
    assert tw.status("CH-01").connected is False
    assert frames[-1].truth.battery_pct == 0.0


def test_better_adhesive_keeps_the_electrode_on():
    def contact_after_peel(adhesion):
        tw = BandageTwin(standard_design().with_changes(adhesive=AdhesiveSpec(adhesion, 900.0)), seed=1)
        tw.schedule(10.0, "disturbance", severity=1.0)
        tw.advance(20.0)
        return tw.truth().contact

    assert contact_after_peel(0.9) > contact_after_peel(0.1)
    weak = BandageTwin(standard_design().with_changes(adhesive=AdhesiveSpec(0.0, 900.0)), seed=1)
    weak.schedule(10.0, "disturbance", severity=1.0)
    weak.advance(20.0)
    assert isinstance(weak.sample().readings["CH-01"], SensorDisconnectedError)


def test_contact_loss_raises_noise():
    def spread(contact):
        tw = BandageTwin(seed=8)
        tw.hold_bench(pathogen_marker=50.0)
        tw.advance(H)
        tw.contact.value = contact
        vals = [tw.read_channel("CH-01").raw_signal for _ in range(300)]
        m = sum(vals) / len(vals)
        return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))

    assert spread(0.5) > 2 * spread(1.0)


def test_electrode_fault_isolates_one_channel():
    tw = BandageTwin(seed=1)
    tw.schedule(60.0, "electrode_fault", channel_id="CH-02")
    frame = tw.step(120.0)
    assert isinstance(frame.readings["CH-02"], SensorDisconnectedError)
    assert not isinstance(frame.readings["CH-01"], Exception)
    assert not isinstance(frame.readings["CH-03"], Exception)
    assert tw.status("CH-02").connected is False


def test_poor_link_drops_packets_and_costs_battery():
    tw = BandageTwin(seed=6)
    tw.link.value = 0.0
    tw.link.target = 0.0
    outcomes = []
    for _ in range(80):
        tw.advance(1.0)
        outcomes.append(tw.sample().readings["CH-01"])
    assert any(isinstance(o, CommsTimeoutError) for o in outcomes)
    assert any(not isinstance(o, Exception) for o in outcomes)  # drop probability caps at 0.8
    assert tw.power.average_current_mA(0.0) > tw.power.average_current_mA(1.0)


def test_fouling_and_ageing_erode_a_constant_signal_over_days():
    tw = BandageTwin(seed=2, max_substep_s=60.0)
    tw.hold_bench(pathogen_marker=100.0)
    tw.advance(H)
    early = tw.read_channel("CH-01", ideal=True).raw_signal
    tw.advance(72 * H)
    late = tw.read_channel("CH-01", ideal=True).raw_signal
    assert late < early * 0.97
    truth = tw.truth().channels["CH-01"]
    assert truth.sensor_health < 0.95 and truth.fouling > 0.05


def test_frames_carry_ground_truth_beside_the_measurement():
    tw = BandageTwin(seed=1)
    f = tw.step()
    assert set(f.truth.channels) == {"CH-01", "CH-02", "CH-03"}
    assert f.readings["CH-01"].device_id == "TWIN"
    assert f.readings["CH-01"].timestamp == f.timestamp
    assert abs(f.readings["CH-01"].raw_signal - f.truth.channels["CH-01"].ideal_au) < 8


# ---------------------------------------------------------------- snapshot
def test_snapshot_is_json_and_resume_is_bit_identical():
    tw = BandageTwin(seed=21)
    tw.schedule(H, "infection", severity=0.7)
    tw.schedule(5 * H, "disturbance", severity=0.5)
    tw.run(2 * H)
    snap = json.loads(tw.to_json())

    resumed = BandageTwin.from_snapshot(snap)
    a = tw.run(6 * H)
    b = resumed.run(6 * H)
    for cid in ("CH-01", "CH-02", "CH-03"):
        assert _readings(a, cid) == _readings(b, cid)
    assert a[-1].truth.bacterial_load == b[-1].truth.bacterial_load


def test_fork_branches_independently_from_the_same_instant():
    tw = BandageTwin(seed=31, max_substep_s=60.0)
    tw.schedule(H, "infection", severity=0.8, duration_h=100)
    tw.run(20 * H)
    treated = tw.fork()
    treated.schedule(treated.t_s, "antibiotic", potency=10.0, duration_h=48)
    untreated_frames = tw.run(24 * H)
    treated_frames = treated.run(24 * H)
    assert treated_frames[-1].truth.bacterial_load < 0.5 * untreated_frames[-1].truth.bacterial_load
    assert tw.events is not treated.events and len(tw.events) == 0


def test_restore_rejects_a_snapshot_from_another_design():
    snap = BandageTwin(standard_design(), seed=1).snapshot()
    other = BandageTwin(fast_response_design(), seed=1)
    with pytest.raises(ValueError, match="different bandage design"):
        other.restore(snap)


def test_pending_events_survive_a_snapshot():
    tw = BandageTwin(seed=1)
    tw.schedule(10 * H, "infection", severity=0.5)
    clone = BandageTwin.from_snapshot(copy.deepcopy(tw.snapshot()))
    assert len(clone.events) == 1 and clone.events.next_time() == 10 * H


# ------------------------------------------------------------------ adapter
def test_adapter_shares_one_instant_across_channels_then_advances():
    tw = BandageTwin(seed=1)
    sensors = sensors_for(tw)
    assert all(isinstance(s, SensorInterface) for s in sensors.values())
    for s in sensors.values():
        s.start_measurement()
    cycle1 = [sensors[c].read_measurement() for c in ("CH-01", "CH-02", "CH-03")]
    cycle2 = [sensors[c].read_measurement() for c in ("CH-01", "CH-02", "CH-03")]
    assert len({m.timestamp for m in cycle1}) == 1
    assert len({m.timestamp for m in cycle2}) == 1
    assert (cycle2[0].timestamp - cycle1[0].timestamp).total_seconds() == tw.design.sample_interval_s
    assert sensors["CH-01"].get_status().battery is not None


def test_adapter_requires_start_and_a_real_channel():
    tw = BandageTwin(seed=1)
    sensors = sensors_for(tw)
    with pytest.raises(RuntimeError):
        sensors["CH-01"].read_measurement()
    with pytest.raises(KeyError):
        from digital_twin.bandage import BandageTwinSensor
        BandageTwinSensor(tw, "NOPE")


# ----------------------------------------------------------------- analysis
def test_characterization_ranks_designs_the_way_the_physics_says():
    std, fast, low = compare([standard_design(), fast_response_design(), low_power_design()])
    s1, f1 = std.channels[0], fast.channels[0]
    assert f1.t90_s < 0.6 * s1.t90_s  # thinner stack responds faster
    assert f1.lod < s1.lod  # bigger electrode is quieter
    assert low.battery_life_h > 3 * std.battery_life_h  # sampling less often lasts longer
    assert fast.battery_life_h < std.battery_life_h
    assert all(c.sensitivity_au_per_unit != 0 and c.t90_s is not None for c in std.channels)


def test_battery_estimate_agrees_with_the_power_model():
    d = standard_design()
    report = characterize(d)
    assert report.battery_life_h == pytest.approx(BandageTwin(d).power.battery_life_h(0.92))


def test_report_table_has_a_row_per_channel():
    table = format_table(compare([standard_design()]))
    assert len(table.splitlines()) == 2 + 3
