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


def test_a_uniform_factor_is_explained_not_failed(pass_csv, tmp_path):
    """A dilution the export does not carry shifts every sample identically.

    Reporting that as a disagreement would cry wolf on every diluted batch, so it
    is surfaced as a scale factor to account for instead.
    """
    src = _scaled(pass_csv, tmp_path, lambda i: 2.0)
    res = checks.quant_crosscheck(masshunter.parse(str(src)), P)
    assert res.outcome != Outcome.FAIL
    row = next(d for d in res.details if d.get("n_compared"))
    assert row["ok"] is None
    assert "same factor" in row["note"]
    assert row["median_ratio"] == pytest.approx(0.5, abs=0.02)   # predicted/reported


def test_scattered_disagreement_is_a_finding(pass_csv, tmp_path):
    """Not a constant factor — that is the signature of a real problem."""
    src = _scaled(pass_csv, tmp_path, lambda i: 1.0 + 0.4 * (i % 3))
    res = checks.quant_crosscheck(masshunter.parse(str(src)), P)
    assert res.outcome == Outcome.FAIL
    bad = [d for d in res.details if d.get("ok") is False]
    assert bad and "not by a constant factor" in bad[0]["note"]
    assert bad[0]["worst_sample"]


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
