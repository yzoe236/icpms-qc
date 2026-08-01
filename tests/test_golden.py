"""Golden outcomes: the all-passing batch is all green; the violations batch
fails exactly the three injected checks (SPEC §7)."""
from icpqc.io import masshunter
from icpqc.qc import engine
from icpqc.qc.checks import Outcome

ALL_CHECKS = {
    "cal_linearity", "cal_low_std", "icv_recovery", "ccv_recovery",
    "ccv_frequency", "icb_ccb_blank", "method_blank", "istd_recovery",
    "lcs_recovery", "dup_rpd", "ms_msd", "serial_dilution", "seq_structure",
}


def _run(csv_path):
    batch = masshunter.parse(str(csv_path))
    results = engine.run(batch, rules="epa6020b")
    return results, {r.check_id: r for r in results}


def test_pass_batch_is_all_green(pass_csv):
    results, by = _run(pass_csv)
    assert set(by) == ALL_CHECKS
    # no serial-dilution sample in the synthetic batch -> loudly not evaluated
    assert by["serial_dilution"].outcome == Outcome.NOT_EVALUATED
    assert "no serial dilution" in by["serial_dilution"].reason
    for cid, r in by.items():
        if cid == "serial_dilution":
            continue
        assert r.outcome == Outcome.PASS, (cid, r.reason, r.details[:3])
    assert engine.verdict(results) == "PASS"


def test_violation_batch_fails_exactly_the_injected_checks(fail_csv):
    results, by = _run(fail_csv)
    assert by["ccv_recovery"].outcome == Outcome.FAIL      # CCV #1 at ~85%
    assert by["istd_recovery"].outcome == Outcome.FAIL     # drift to ~65%
    assert by["dup_rpd"].outcome == Outcome.FAIL           # ~30% RPD
    for cid in ALL_CHECKS - {"ccv_recovery", "istd_recovery", "dup_rpd", "serial_dilution"}:
        assert by[cid].outcome == Outcome.PASS, (cid, by[cid].reason, by[cid].details[:3])
    assert engine.verdict(results) == "FAIL"


def test_failing_details_name_the_culprits(fail_csv):
    _, by = _run(fail_csv)
    ccv_fails = [d for d in by["ccv_recovery"].details if d.get("ok") is False]
    assert ccv_fails and all(d["sample"] == "CCV-50 #1" for d in ccv_fails)
    istd_fails = [d for d in by["istd_recovery"].details
                  if d.get("ok") is False and "sample" in d]
    assert istd_fails and all(d["sample"].startswith("S01") for d in istd_fails)
