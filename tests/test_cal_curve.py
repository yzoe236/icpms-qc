"""cal_back_calc / cal_heteroscedasticity: what the correlation coefficient hides.

An ICP-MS calibration is heteroscedastic by construction — counting statistics
alone make the bottom of the curve noisier in relative terms. r is computed
across the whole range and is dominated by the top of it, so a curve whose
lowest standard back-calculates to half its nominal value still reports
r > 0.999. These tests pin that down: the same batch that sails through
cal_linearity is caught on its residuals.
"""
from icpqc.io import masshunter
from icpqc.qc import checks
from icpqc.qc.checks import Outcome

BE = "9 Be Conc. [ppb]"
LOW_STD = "Cal Std 1 ppb"
STD_5 = "Cal Std 5 ppb"
STD_50 = "Cal Std 50 ppb"
STD_100 = "Cal Std 100 ppb"


def _bc(**over):
    base = {"window_pct": [90, 110], "low_window_pct": [70, 130], "min_levels": 3}
    return {**base, **over}


def _het(**over):
    base = {"max_ratio": 3.0, "min_low_err_pct": 10.0, "min_levels": 4,
            "on_exceed": "warn"}
    return {**base, **over}


def test_reference_batch_passes_both(pass_csv):
    batch = masshunter.parse(str(pass_csv))
    assert checks.cal_back_calc(batch, _bc()).outcome == Outcome.PASS
    assert checks.cal_heteroscedasticity(batch, _het()).outcome == Outcome.PASS


def test_a_ratio_alone_is_not_a_finding(pass_csv):
    """Regression: the clean batch has an analyte whose halves differ 15x.

    52 Cr lands at 1.5% relative error low and 0.1% high purely by chance — a
    ratio of 15 on two excellent numbers. Without an absolute floor this check
    cried wolf on a curve that is entirely fine, and took the golden and CLI
    tests down with it.
    """
    batch = masshunter.parse(str(pass_csv))
    row = next(d for d in checks.cal_heteroscedasticity(batch, _het()).details
               if d["analyte"] == "52 Cr [He]")
    assert row["ratio"] > 3.0                         # disproportionate...
    assert row["low_rel_err_pct"] < 10.0              # ...but tiny
    assert row["ok"] is True
    assert "not actionable" in row["note"]


def test_r_passes_while_the_bottom_of_the_curve_reads_half(pass_csv, edited_csv):
    """The whole reason these checks exist."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {(LOW_STD, BE): "0.5"})))

    lin = checks.cal_linearity(batch, {"min_r": 0.998})
    assert lin.outcome == Outcome.PASS                # r never noticed
    assert next(d for d in lin.details if d["analyte"] == "9 Be")["r"] >= 0.998

    bc = checks.cal_back_calc(batch, _bc())
    assert bc.outcome == Outcome.FAIL                 # the residual did
    row = next(d for d in bc.details
               if d["analyte"] == "9 Be" and d["level"] == 1.0)
    assert row["ok"] is False
    assert row["recovery_pct"] == 50.0
    assert row["lowest_level"] is True


def test_low_end_error_growth_warns_and_names_the_remedy(pass_csv, edited_csv):
    batch = masshunter.parse(str(edited_csv(pass_csv, {
        (LOW_STD, BE): "1.25", (STD_5, BE): "6.0"})))
    res = checks.cal_heteroscedasticity(batch, _het())
    assert res.outcome == Outcome.WARN
    assert "weighted fit indicated" in res.reason
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["ratio"] > 3.0
    assert row["ok"] is None                          # diagnostic, not a failure
    assert "weighted least squares" in row["note"]


def test_on_exceed_fail_makes_it_binding(pass_csv, edited_csv):
    batch = masshunter.parse(str(edited_csv(pass_csv, {
        (LOW_STD, BE): "1.25", (STD_5, BE): "6.0"})))
    res = checks.cal_heteroscedasticity(batch, _het(on_exceed="fail"))
    assert res.outcome == Outcome.FAIL
    assert next(d for d in res.details if d["analyte"] == "9 Be")["ok"] is False


def test_exact_top_half_is_undefined_not_infinite(pass_csv, edited_csv):
    """A top half that fits perfectly makes the ratio undefined, not enormous."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {
        (STD_50, BE): "50", (STD_100, BE): "100"})))
    row = next(d for d in checks.cal_heteroscedasticity(batch, _het()).details
               if d["analyte"] == "9 Be")
    assert row["ratio"] is None
    assert row["ok"] is None
    assert "undefined" in row["note"]


def test_too_few_levels_is_not_evaluated(pass_csv):
    batch = masshunter.parse(str(pass_csv))
    assert checks.cal_back_calc(batch, _bc(min_levels=9)).outcome == Outcome.NOT_EVALUATED
    assert checks.cal_heteroscedasticity(
        batch, _het(min_levels=9)).outcome == Outcome.NOT_EVALUATED


def test_censored_standard_bounds_recovery_but_is_not_a_residual(pass_csv, edited_csv):
    """A non-detect in a standard bounds recovery from above; it is not a residual."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {(LOW_STD, BE): "<0.2"})))

    row = next(d for d in checks.cal_heteroscedasticity(batch, _het()).details
               if d["analyte"] == "9 Be")
    assert row["n_low"] == 1                          # only the 5 ppb level reported
    assert row["ok"] is None
    assert "need >=2 reported levels" in row["note"]

    low = next(d for d in checks.cal_back_calc(batch, _bc()).details
               if d["analyte"] == "9 Be" and d["level"] == 1.0)
    assert low["ok"] is False                         # <0.2 at 1 ppb: at most 20%
    assert "at most 20.0%" in low["note"]
