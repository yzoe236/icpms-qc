"""End-to-end CLI: exit codes, report files, JSON contract."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _run_cli(csv_path, out_dir):
    return subprocess.run(
        [sys.executable, "-m", "icpqc.cli", "check", str(csv_path), "--out", str(out_dir)],
        capture_output=True, text=True, cwd=REPO,
    )


def test_pass_batch_exits_zero(pass_csv, tmp_path):
    out = tmp_path / "pass_out"
    r = _run_cli(pass_csv, out)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads((out / "qc_report.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "0.1"
    assert data["verdict"] == "PASS"
    assert len(data["checks"]) == 13
    html = (out / "qc_report.html").read_text(encoding="utf-8")
    assert "icpqc QC report" in html and "ccv_recovery" in html


def test_violation_batch_exits_two(fail_csv, tmp_path):
    out = tmp_path / "fail_out"
    r = _run_cli(fail_csv, out)
    assert r.returncode == 2, r.stdout + r.stderr
    data = json.loads((out / "qc_report.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "FAIL"
    failed = {c["check_id"] for c in data["checks"] if c["outcome"] == "FAIL"}
    assert failed == {"ccv_recovery", "istd_recovery", "dup_rpd"}


def test_bad_input_exits_one(tmp_path):
    r = _run_cli(tmp_path / "does_not_exist.csv", tmp_path / "o")
    assert r.returncode == 1
    assert "error" in r.stderr.lower()
