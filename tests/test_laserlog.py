"""Laser log parsing and laser_log_alignment.

The laser and the mass spectrometer keep separate clocks. Which counts belong to
which ablation is always a reconstruction, and when it slips every concentration
after the slip is attributed to the wrong spot while the report stays green.
These tests pin down the complaint.
"""
import sys
from pathlib import Path

import pytest

from icpms_qc.io import laserlog
from icpms_qc.model import Batch, Sample, SampleType
from icpms_qc.qc import checks
from icpms_qc.qc.checks import Outcome

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from gen_synthetic_laserlog import generate as gen_log  # noqa: E402

SAMPLES = ["S001", "S002", "NIST612", "S003"]


@pytest.fixture
def log_path(tmp_path):
    def _make(**over):
        p = tmp_path / f"log{len(list(tmp_path.iterdir()))}.csv"
        gen_log(str(p), over.pop("samples", SAMPLES), **over)
        return p
    return _make


def _batch(names, log=None) -> Batch:
    b = Batch(source_path="synthetic", template_id="t", instrument_family="la-icpms")
    b.samples = [Sample(name=n, seq_index=i, type=SampleType.SAMPLE)
                 for i, n in enumerate(names, start=1)]
    b.laser_log = log
    return b


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parses_sequences_and_ablations(log_path):
    log = laserlog.parse(str(log_path(spots=5)))
    assert len(log.sequences) == 4
    assert len(log.ablations) == 20                 # 4 sequences x 5 spots
    assert [s.comment for s in log.sequences] == SAMPLES
    assert all(len(s.ablations) == 5 for s in log.sequences)
    assert log.warnings == []


def test_sparse_columns_are_carried_forward(log_path):
    """Sequence Number and Comment appear once; every ablation must inherit them."""
    log = laserlog.parse(str(log_path(spots=3)))
    first = log.sequences[0]
    assert all(a.comment == "S001" and a.sequence == 1 for a in first.ablations)


def test_ablation_spans_are_on_to_off(log_path):
    log = laserlog.parse(str(log_path(spots=2)))
    for a in log.ablations:
        assert a.end is not None
        assert a.duration_s == pytest.approx(2.5, abs=0.01)


def test_environment_series_is_captured(log_path):
    log = laserlog.parse(str(log_path(spots=2)))
    assert log.environment
    assert log.environment[0].mfc1 == pytest.approx(0.9)
    assert log.environment[0].cell_pressure == pytest.approx(101.3)


def test_header_sniff_tells_the_two_files_apart(log_path, pass_csv):
    """The CLI takes two CSVs; handing it the wrong one must fail immediately."""
    assert laserlog.looks_like_laser_log(str(log_path(samples=["A"], spots=1)))
    assert not laserlog.looks_like_laser_log(str(pass_csv))


def test_not_a_laser_log_is_a_clear_error(tmp_path, pass_csv):
    with pytest.raises(ValueError, match="not a laser log"):
        laserlog.parse(str(pass_csv))


# ── granularity ──────────────────────────────────────────────────────────────

def test_auto_granularity_picks_sequence_when_rows_match_patterns(log_path):
    log = laserlog.parse(str(log_path(spots=5)))
    res = checks.laser_log_alignment(_batch(SAMPLES, log), {})
    assert res.outcome == Outcome.PASS
    assert res.details[0]["granularity"] == "sequence"


def test_auto_granularity_picks_ablation_when_rows_match_spots(log_path):
    """A spot-per-row reduction is the other legitimate reading of the same log."""
    log = laserlog.parse(str(log_path(samples=["S001"], spots=6)))
    res = checks.laser_log_alignment(_batch([f"S001-{i}" for i in range(1, 7)], log), {})
    assert res.details[0]["granularity"] == "ablation"
    assert res.outcome == Outcome.PASS


def test_matching_neither_count_is_the_finding(log_path):
    """Not resolved by picking the closer number — the disagreement IS the result."""
    log = laserlog.parse(str(log_path(spots=5)))       # 4 sequences, 20 ablations
    res = checks.laser_log_alignment(_batch(["a", "b", "c"], log), {})
    assert res.outcome == Outcome.FAIL
    assert "match neither" in res.reason
    row = res.details[0]
    assert (row["result_rows"], row["log_sequences"], row["log_ablations"]) == (3, 4, 20)


# ── the failures this check exists for ───────────────────────────────────────

def test_a_dropped_sequence_is_caught(log_path):
    """A lost trigger: the laser recorded 3 patterns, the results claim 4."""
    log = laserlog.parse(str(log_path(drop_sequence=3)))
    res = checks.laser_log_alignment(_batch(SAMPLES, log), {})
    assert res.outcome == Outcome.FAIL


def test_off_by_one_assignment_is_named_position_by_position(log_path):
    """The results are shifted by one: every position disagrees, and it is shown."""
    log = laserlog.parse(str(log_path(spots=5)))
    shifted = ["S000"] + SAMPLES[:-1]                  # same count, wrong alignment
    res = checks.laser_log_alignment(_batch(shifted, log), {})
    assert res.outcome == Outcome.FAIL
    bad = [d for d in res.details if d.get("position")]
    assert bad and bad[0]["log_comment"] == "S001" and bad[0]["result_sample"] == "S000"
    # the standard moved position — exactly the case that silently ruins a run
    assert any(d["log_comment"] == "NIST612" for d in bad)


def test_cosmetic_name_differences_do_not_trip_it(log_path):
    log = laserlog.parse(str(log_path(samples=["Image Raster1"], spots=3)))
    res = checks.laser_log_alignment(_batch(["image_raster1"], log), {})
    assert res.outcome == Outcome.PASS


def test_an_aborted_ablation_is_surfaced(log_path):
    log = laserlog.parse(str(log_path(spots=5, short_ablation=(7, 0.4))))
    res = checks.laser_log_alignment(_batch(SAMPLES, log), {})
    row = next(d for d in res.details if d.get("outliers"))
    assert row["outliers"] == 1 and "#7" in row["note"]
    assert res.outcome == Outcome.PASS          # informational, not a batch failure


# ── absence ──────────────────────────────────────────────────────────────────

def test_no_log_is_not_evaluated_not_passed():
    res = checks.laser_log_alignment(_batch(SAMPLES), {})
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "--laser-log" in res.reason
