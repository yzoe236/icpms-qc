import csv
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))            # import icpms_qc without installing
sys.path.insert(0, str(REPO / "tools"))  # import the generator as a module

from gen_synthetic_data import generate  # noqa: E402


@pytest.fixture(scope="session")
def pass_csv(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("data") / "pass.csv"
    generate(str(p), violations=False, seed=42)
    return p


@pytest.fixture(scope="session")
def fail_csv(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("data") / "fail.csv"
    generate(str(p), violations=True, seed=42)
    return p


@pytest.fixture
def edited_csv(tmp_path):
    """Copy an export, overwriting individual cells: {(sample, column): value}.

    Lets a test inject one odd value — a "<0.05", a wild recovery — into an
    otherwise-passing batch, so the assertion is about that value alone.
    """
    counter = iter(range(1000))

    def _edit(src: Path, edits: dict[tuple[str, str], str]) -> Path:
        with open(src, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fieldnames, rows = reader.fieldnames, list(reader)
        for (sample, column), value in edits.items():
            matched = [r for r in rows if r["Sample Name"] == sample]
            assert matched, f"no sample named {sample!r} in {src}"
            assert column in fieldnames, f"no column named {column!r} in {src}"
            for r in matched:
                r[column] = value
        dest = tmp_path / f"edited_{next(counter)}.csv"
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return dest

    return _edit
