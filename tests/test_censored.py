"""Non-detects ("<0.05", "ND") are results, not missing data.

MassHunter censors a below-detection result rather than printing a number. Read
naively that becomes None — indistinguishable from an analyte nobody measured —
so a clean blank and an absent blank produce the same report. They must not.
"""
from icpqc.io import masshunter
from icpqc.io.masshunter import _parse_conc
from icpqc.model import SampleType
from icpqc.qc import checks
from icpqc.qc.checks import Outcome

BE = "9 Be Conc. [ppb]"
LOQ = {"loq_ppb": {"default": 0.1}, "limit": "LOQ"}


def test_parse_conc_forms():
    assert _parse_conc("0.05") == (0.05, False, None)
    assert _parse_conc("<0.05") == (None, True, 0.05)
    assert _parse_conc("< 0.05") == (None, True, 0.05)
    assert _parse_conc("ND") == (None, True, None)
    assert _parse_conc("n.d.") == (None, True, None)
    # genuinely absent — not the same thing as a non-detect
    assert _parse_conc("") == (None, False, None)
    assert _parse_conc("N/A") == (None, False, None)


def test_censored_result_survives_parsing(pass_csv, edited_csv):
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CCB #1", BE): "<0.05"})))
    r = batch.find_sample("CCB #1", SampleType.CCB).results["9 Be"]
    assert (r.conc, r.below_dl, r.dl) == (None, True, 0.05)
    assert r.upper_bound == 0.05


def test_blank_below_its_detection_limit_passes(pass_csv, edited_csv):
    """DL under the threshold: whatever the true value is, the blank clears."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CCB #1", BE): "<0.05"})))
    res = checks.icb_ccb_blank(batch, LOQ)
    row = next(d for d in res.details
               if d["sample"] == "CCB #1" and d["analyte"] == "9 Be")
    assert row["ok"] is True
    assert "non-detect" in row["note"]
    assert res.outcome == Outcome.PASS


def test_blank_censored_above_the_threshold_is_undecidable(pass_csv, edited_csv):
    """DL of 0.5 against a limit of 0.1 proves nothing — and must not claim to."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CCB #1", BE): "<0.5"})))
    res = checks.icb_ccb_blank(batch, LOQ)
    row = next(d for d in res.details
               if d["sample"] == "CCB #1" and d["analyte"] == "9 Be")
    assert row["ok"] is None
    assert "cannot decide" in row["note"]
    assert res.outcome == Outcome.PASS          # other rows still decide it


def test_non_detect_in_a_ccv_fails_on_its_upper_bound(pass_csv, edited_csv):
    """A standard that came back non-detect cannot have recovered 90-110%."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CCV-50 #1", BE): "<0.05"})))
    res = checks.ccv_recovery(batch, {"window_pct": [90, 110]})
    row = next(d for d in res.details
               if d["sample"] == "CCV-50 #1" and d["analyte"] == "9 Be")
    assert row["ok"] is False
    assert "at most 0.1%" in row["note"]
    assert res.outcome == Outcome.FAIL


def test_missing_result_is_still_reported_as_missing(pass_csv, edited_csv):
    """The regression this guards: absent must never be read as non-detect."""
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CCB #1", BE): ""})))
    r = batch.find_sample("CCB #1", SampleType.CCB).results["9 Be"]
    assert (r.conc, r.below_dl) == (None, False)
    row = next(d for d in checks.icb_ccb_blank(batch, LOQ).details
               if d["sample"] == "CCB #1" and d["analyte"] == "9 Be")
    assert row["ok"] is None and row["note"] == "no result"
