"""quant_crosscheck — recompute from counts, compare with what was reported.

Every other check trusts the reported concentration. This one asks where it came
from. The instrument software cannot raise these findings itself: it *is* what
produced the number, applying the parameters it was given without doubting them.
"""
import csv

import pytest

from icpms_qc.io import masshunter
from icpms_qc.qc import checks
from icpms_qc.qc.checks import Outcome

P = {"max_deviation_pct": 15, "min_levels": 3, "min_conc_x_loq": 10,
     "uniform_ratio_cv_pct": 5, "loq_ppb": {"default": 0.1}}


def _scaled(src, tmp_path, factor_fn, name="scaled.csv"):
    """Copy an export, rescaling every reported sample concentration."""
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    fields = list(rows[0])
    cols = [c for c in fields if c.endswith("Conc. [ppb]")]
    for i, r in enumerate(rows):
        # Everything the check compares — that is, everything except the
        # calibration itself. Scaling only the "Sample" rows would leave the QC
        # aliquots at 1.0 and make the offset genuinely non-uniform.
        if r["Type"] in {"CalStd", "CalBlk"}:
            continue
        for c in cols:
            if r[c]:
                r[c] = f"{float(r[c]) * factor_fn(i):.4f}"
    dest = tmp_path / name
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return dest


def test_recomputation_agrees_with_the_reference_batch(pass_csv):
    res = checks.quant_crosscheck(masshunter.parse(str(pass_csv)), P)
    assert res.outcome == Outcome.PASS
    head = res.details[0]
    assert head["cal_points"] >= 3
    rows = [d for d in res.details if d.get("n_compared")]
    assert rows and all(d["max_deviation_pct"] < 15 for d in rows)


def test_a_uniform_factor_is_the_finding(pass_csv, tmp_path):
    """The one disagreement this comparison can prove.

    Every unknown in the vendor's arithmetic — weighting, curve type, excluded
    standards, interference corrections — cancels in a ratio. So a column that is
    out by the same factor everywhere was scaled by something: a dilution factor,
    a unit, a transcription.
    """
    src = _scaled(pass_csv, tmp_path, lambda i: 2.0)
    res = checks.quant_crosscheck(masshunter.parse(str(src)), P)
    assert res.outcome == Outcome.FAIL
    assert "same factor" in res.reason
    row = next(d for d in res.details if d.get("scale_factor"))
    assert row["ok"] is False
    assert row["scale_factor"] == pytest.approx(0.5, abs=0.02)   # predicted/reported
    assert "scales the sample, not one mass" in row["note"]


def test_one_analyte_alone_is_not_a_dilution(pass_csv, tmp_path):
    """A single mass at a constant offset is per-mass arithmetic, not a scale error.

    Real batches put exactly the classically interference-corrected masses here —
    ArO on 56 Fe, ClO on 51 V, ArC on 52 Cr — each with its own different factor.
    A dilution multiplies the whole sample, so it cannot single one mass out.
    """
    import csv as _csv
    rows = list(_csv.DictReader(open(pass_csv, newline="", encoding="utf-8")))
    fields = list(rows[0])
    for r in rows:
        if r["Type"] not in {"CalStd", "CalBlk"} and r["9 Be Conc. [ppb]"]:
            r["9 Be Conc. [ppb]"] = f"{float(r['9 Be Conc. [ppb]']) * 1.6:.4f}"
    src = tmp_path / "one.csv"
    with open(src, "w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(rows)

    res = checks.quant_crosscheck(masshunter.parse(str(src)), P)
    assert res.outcome == Outcome.WARN            # reported, not blamed
    row = next(d for d in res.details if d.get("scale_factor"))
    assert row["ok"] is None
    assert "interference correction" in row["note"]


def test_scattered_disagreement_is_reported_but_never_failed(pass_csv, tmp_path):
    """Scatter is not evidence, and real batches produce plenty of it.

    Checking against real exports showed individual samples differing for reasons
    the file does not record. Failing on that would blame the batch for what the
    export omits, so it is shown and explained instead.
    """
    src = _scaled(pass_csv, tmp_path, lambda i: 1.0 + 0.4 * (i % 3))
    res = checks.quant_crosscheck(masshunter.parse(str(src)), P)
    assert res.outcome != Outcome.FAIL
    noted = [d for d in res.details if "not by a constant factor" in (d.get("note") or "")]
    assert noted and all(d["ok"] is None for d in noted)
    assert "omits" in noted[0]["note"]


def test_an_export_without_intensities_cannot_be_cross_checked(pass_csv, tmp_path):
    """Say so, rather than pass quietly — half the inputs are simply absent."""
    lines = pass_csv.read_text(encoding="utf-8").splitlines()
    keep = [i for i, c in enumerate(lines[0].split(","))
            if " CPS" not in c]                       # strip CPS and CPS RSD
    stripped = tmp_path / "no_cps.csv"
    stripped.write_text("\n".join(",".join(r.split(",")[i] for i in keep)
                                  for r in lines) + "\n", encoding="utf-8")
    res = checks.quant_crosscheck(masshunter.parse(str(stripped)), P)
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "no raw intensities" in res.reason


def test_calibration_mode_is_stated(pass_csv):
    """Which normalization was used changes every number — it must be on the report."""
    res = checks.quant_crosscheck(masshunter.parse(str(pass_csv)), P)
    mode = res.details[0]["calibration"]
    # the reference batch ships three ISTDs, so the assignment is not exported
    assert "external calibration" in mode and "multiple ISTDs" in mode
