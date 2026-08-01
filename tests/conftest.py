import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))            # import icpqc without installing
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
