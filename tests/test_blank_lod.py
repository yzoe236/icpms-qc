"""blank_derived_lod: the check that audits the configuration, not the batch.

3-sigma/10-sigma of the post-calibration blanks, compared against the LOQ the
rule pack declares. A reporting limit carried over from a better day makes every
blank threshold and every RPD cutoff downstream of it optimistic, and no other
check in the catalog would notice.
"""
from icpms_qc.io import masshunter
from icpms_qc.qc import checks
from icpms_qc.qc.checks import Outcome

BE = "9 Be Conc. [ppb]"


def _params(**over):
    base = {"blank_types": ["ICB", "CCB"], "k_lod": 3, "k_loq": 10,
            "min_n": 3, "on_exceed": "warn", "loq_ppb": {"default": 0.1}}
    return {**base, **over}


def test_reference_batch_loq_is_defensible(pass_csv):
    res = checks.blank_derived_lod(masshunter.parse(str(pass_csv)), _params())
    assert res.outcome == Outcome.PASS
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["n"] == 3                      # ICB + CCB #1 + CCB #2
    assert row["derived_loq"] < row["configured_loq"]
    assert row["derived_lod"] < row["derived_loq"]


def test_optimistic_configured_loq_warns(pass_csv):
    """Blank scatter implying a higher LOQ than configured is worth saying."""
    res = checks.blank_derived_lod(masshunter.parse(str(pass_csv)),
                                   _params(loq_ppb={"default": 0.0001}))
    assert res.outcome == Outcome.WARN
    assert "implies an LOQ above the configured value" in res.reason
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["ok"] is None                  # flagged, but not a batch failure
    assert "higher LOQ than the pack configures" in row["note"]


def test_on_exceed_fail_makes_it_a_failure(pass_csv):
    res = checks.blank_derived_lod(masshunter.parse(str(pass_csv)),
                                   _params(loq_ppb={"default": 0.0001},
                                           on_exceed="fail"))
    assert res.outcome == Outcome.FAIL
    assert all(d["ok"] is False for d in res.details)


def test_too_few_blanks_is_not_evaluated(pass_csv):
    res = checks.blank_derived_lod(masshunter.parse(str(pass_csv)), _params(min_n=9))
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "need >=9 post-calibration blanks" in res.reason


def test_censored_blanks_do_not_become_a_standard_deviation(pass_csv, edited_csv):
    """Non-detects carry no value; three of them are not three measurements."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {
        ("ICB", BE): "<0.01", ("CCB #1", BE): "<0.01", ("CCB #2", BE): "<0.01"})))
    res = checks.blank_derived_lod(batch, _params())
    row = next(d for d in res.details if d["analyte"] == "9 Be")
    assert row["ok"] is None
    assert row["n"] == 0
    assert "non-detects carry no value" in row["note"]
    assert res.outcome == Outcome.PASS        # the other analytes still decide


def test_unknown_blank_type_is_reported(pass_csv):
    res = checks.blank_derived_lod(masshunter.parse(str(pass_csv)),
                                   _params(blank_types=["RINSE"]))
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "RINSE" in res.reason
