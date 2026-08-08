"""precision_rsd and instrument_flags — the blind spot a real batch exposed.

Validating against two real MassHunter exports showed icpms-qc reporting nothing at
all while the instrument software itself raised 1218 objections, every one of
them about precision or calibration. Two causes: no precision check existed, and
the templates discarded the %RSD columns before the engine ever saw them. These
tests pin both shut.
"""
import pytest

from icpms_qc.io import masshunter
from icpms_qc.io.masshunter import parse_instrument_flags
from icpms_qc.qc import checks
from icpms_qc.qc.checks import Outcome

BE_RSD = "9 Be CPS RSD"
BE_CPS = "9 Be CPS"


def _params(**over):
    base = {"max_rsd_pct": 5.0, "min_intensity_cps": 1000, "min_conc_x_loq": 10}
    return {**base, **over}


# ── the data survives parsing ────────────────────────────────────────────────

def test_rsd_columns_reach_the_model(pass_csv):
    """The regression: 'RSD$' in ignore_patterns silently dropped every one."""
    batch = masshunter.parse(str(pass_csv))
    with_rsd = [r for s in batch.samples for r in s.results.values()
                if r.rsd_pct is not None]
    assert len(with_rsd) == len(batch.samples) * len(batch.analytes)


def test_reference_batch_precision_passes(pass_csv):
    res = checks.precision_rsd(masshunter.parse(str(pass_csv)), _params())
    assert res.outcome == Outcome.PASS
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["n_assessed"] > 0 and row["n_over"] == 0


def test_an_imprecise_analyte_fails_and_is_named(pass_csv, edited_csv):
    batch = masshunter.parse(str(edited_csv(pass_csv, {("S001", BE_RSD): "22.5"})))
    res = checks.precision_rsd(batch, _params())
    assert res.outcome == Outcome.FAIL
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["n_over"] == 1
    assert row["max_rsd_pct"] == pytest.approx(22.5)
    assert row["worst_sample"] == "S001"


def test_low_signal_is_not_assessed_rather_than_failed(pass_csv, edited_csv):
    """A blank's RSD is counting noise. Reporting it would bury the real findings."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {
        ("S001", BE_RSD): "180.0", ("S001", BE_CPS): "12"})))
    res = checks.precision_rsd(batch, _params())
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["max_rsd_pct"] != pytest.approx(180.0)      # excluded from the worst
    assert res.outcome == Outcome.PASS


def test_no_rsd_columns_is_not_evaluated_not_passed(pass_csv, tmp_path):
    """An export without precision data must say so, not report a quiet pass."""
    text = pass_csv.read_text(encoding="utf-8").splitlines()
    keep = [i for i, c in enumerate(text[0].split(",")) if not c.endswith("CPS RSD")]
    stripped = tmp_path / "no_rsd.csv"
    stripped.write_text("\n".join(",".join(r.split(",")[i] for i in keep)
                                  for r in text) + "\n", encoding="utf-8")
    res = checks.precision_rsd(masshunter.parse(str(stripped)), _params())
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "no %RSD columns" in res.reason


# ── the instrument's own verdict ─────────────────────────────────────────────

LABELS = ["27  Al  [ No Gas ]", "66  Zn  [ No Gas ]", "7  Li  [ No Gas ]"]


def test_flags_split_on_known_labels_not_on_guesswork():
    """The blob has no delimiter: '= 5.00' runs straight into mass '66'."""
    blob = ("27  Al  [ No Gas ] :  CPS RSD value = 5.39 is over the allowed maximum "
            "= 5.0066  Zn  [ No Gas ] :  CPS RSD value = 12.10 is over the allowed "
            "maximum = 5.00")
    flags = parse_instrument_flags(blob, LABELS)
    assert len(flags) == 2
    assert flags[0].analyte == "27  Al  [ No Gas ]"
    assert (flags[0].value, flags[0].limit) == (5.39, 5.00)
    assert flags[1].analyte == "66  Zn  [ No Gas ]"
    assert (flags[1].value, flags[1].limit) == (12.10, 5.00)   # not 5.0066


def test_both_directions_and_negative_values():
    blob = ("7  Li  [ No Gas ] :  Calibration Curve Fit R value = -0.286775 is "
            "below the allowed minimum = 0.950000")
    f = parse_instrument_flags(blob, LABELS)[0]
    assert f.metric == "Calibration Curve Fit R"
    assert f.value == pytest.approx(-0.286775) and f.limit == pytest.approx(0.95)
    assert f.direction == "low"
    assert "<" in f.describe()


def test_range_wording_without_a_numeric_limit():
    blob = "66  Zn  [ No Gas ] :  Concentration value = 2202.42 is over the calibration range"
    f = parse_instrument_flags(blob, LABELS)[0]
    assert f.metric == "Concentration outside calibration range"
    assert f.value == pytest.approx(2202.42) and f.limit is None
    assert f.direction == "high"


def test_unknown_wording_is_carried_not_dropped():
    blob = "27  Al  [ No Gas ] :  something the parser has never seen"
    f = parse_instrument_flags(blob, LABELS)[0]
    assert f.value is None
    assert "never seen" in f.text          # the vendor's objection still survives


def test_no_flag_column_reads_differently_from_no_flags(pass_csv):
    """'The instrument said nothing' and 'we never asked' must not look alike."""
    batch = masshunter.parse(str(pass_csv))
    assert batch.flags_column_mapped is False
    res = checks.instrument_flags(batch, {})
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "maps no instrument-flag column" in res.reason

    batch.flags_column_mapped = True       # column present, instrument had nothing
    res = checks.instrument_flags(batch, {})
    assert res.outcome == Outcome.PASS
    assert "raised no QC objection" in res.details[0]["note"]


def test_flags_are_reported_but_not_decisive_by_default(pass_csv):
    from icpms_qc.model import InstrumentFlag
    batch = masshunter.parse(str(pass_csv))
    batch.flags_column_mapped = True
    batch.samples[0].instrument_flags = [
        InstrumentFlag("9 Be", "CPS RSD", 12.1, 5.0, "high")]

    res = checks.instrument_flags(batch, {})
    assert res.outcome == Outcome.WARN            # vendor thresholds, not ours
    assert all(d["ok"] is not False for d in res.details)

    res = checks.instrument_flags(batch, {"on_flag": "fail"})
    assert res.outcome == Outcome.FAIL            # opt in to making them binding
