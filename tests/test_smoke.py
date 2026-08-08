"""Smoke tests: the package imports, the model works, the generator generates."""
import csv
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_model_basics():
    from icpms_qc.model import Batch, SampleType

    b = Batch(source_path="x", template_id="masshunter_quant_wide",
              instrument_family="agilent-masshunter")
    assert b.of_type(SampleType.CCV) == []
    assert b.warnings == []


def test_generator_produces_reference_layout(tmp_path):
    out = tmp_path / "demo.csv"
    r = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gen_synthetic_data.py"), str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "wrote" in r.stdout
    with open(out, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) > 25
    assert "Sample Name" in rows[0]
    assert any(k.endswith("Conc. [ppb]") for k in rows[0])
    assert {row["Type"] for row in rows} >= {"CalStd", "CCV", "CCB", "MB", "LCS", "Sample", "Dup"}
