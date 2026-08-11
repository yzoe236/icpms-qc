"""Golden outcomes: the all-passing batch is all green; the violations batch
fails exactly the three injected checks (SPEC §7)."""
from icpms_qc.io import masshunter
from icpms_qc.qc import engine
from icpms_qc.qc.checks import Outcome

ALL_CHECKS = {
    "cal_linearity", "cal_back_calc", "cal_heteroscedasticity", "cal_low_std",
    "icv_recovery", "ccv_recovery", "ccv_frequency", "icb_ccb_blank",
    "method_blank", "blank_derived_lod", "precision_rsd", "instrument_flags",
    "istd_recovery", "lcs_recovery",
    "quant_crosscheck", "crm_recovery", "dup_rpd", "ms_msd", "serial_dilution",
    "oes_line_agreement", "seq_structure",
}

#: Checks with nothing to work on in a synthetic solution batch. Each must say so
#: out loud — a NOT_EVALUATED here is the correct answer, a PASS would be a lie.
INAPPLICABLE = {
    "serial_dilution": "no serial dilution",
    "oes_line_agreement": "more than one emission line",
    # The reference layout carries no instrument-flag column, so the vendor's own
    # verdict is unavailable — which must read differently from "it had none".
    "instrument_flags": "maps no instrument-flag column",
}


def _run(csv_path):
    batch = masshunter.parse(str(csv_path))
    results = engine.run(batch, rules="epa6020b")
    return results, {r.check_id: r for r in results}


def test_pass_batch_is_all_green(pass_csv):
    results, by = _run(pass_csv)
    assert set(by) == ALL_CHECKS
    for cid, fragment in INAPPLICABLE.items():
        assert by[cid].outcome == Outcome.NOT_EVALUATED, cid
        assert fragment in by[cid].reason
    for cid, r in by.items():
        if cid in INAPPLICABLE:
            continue
        assert r.outcome == Outcome.PASS, (cid, r.reason, r.details[:3])
    assert engine.verdict(results) == "PASS"


def test_violation_batch_fails_exactly_the_injected_checks(fail_csv):
    results, by = _run(fail_csv)
    assert by["ccv_recovery"].outcome == Outcome.FAIL      # CCV #1 at ~85%
    assert by["istd_recovery"].outcome == Outcome.FAIL     # drift to ~65%
    assert by["dup_rpd"].outcome == Outcome.FAIL           # ~30% RPD
    for cid in ALL_CHECKS - {"ccv_recovery", "istd_recovery", "dup_rpd"} - set(INAPPLICABLE):
        assert by[cid].outcome == Outcome.PASS, (cid, by[cid].reason, by[cid].details[:3])
    assert engine.verdict(results) == "FAIL"


def test_failing_details_name_the_culprits(fail_csv):
    _, by = _run(fail_csv)
    ccv_fails = [d for d in by["ccv_recovery"].details if d.get("ok") is False]
    assert ccv_fails and all(d["sample"] == "CCV-50 #1" for d in ccv_fails)
    istd_fails = [d for d in by["istd_recovery"].details
                  if d.get("ok") is False and "sample" in d]
    assert istd_fails and all(d["sample"].startswith("S01") for d in istd_fails)
