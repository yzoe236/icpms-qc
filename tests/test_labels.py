"""Analyte label parsing: single-quad, collision modes, and triple-quad MS/MS.

The 8800/8900 write "78 -> 78 Se [He]" — Q1 mass, Q2 mass, cell mode. A parser
that only understands "75 As [He]" reads those as no mass and no element at all,
and every check that groups by element goes silently blind on a QQQ batch.
"""
import re

import pytest

from icpqc.io import masshunter
from icpqc.io.masshunter import _analyte_from_label as parse
from icpqc.qc import checks
from icpqc.qc.checks import Outcome


@pytest.mark.parametrize("label,mass,shift,element,mode", [
    ("9 Be",                9,   None, "Be", None),
    ("75 As [He]",          75,  None, "As", "He"),
    ("56  Fe  [ No Gas ]",  56,  None, "Fe", "No Gas"),   # padded + spaced mode
    ("238 U [NoGas]",       238, None, "U",  "NoGas"),
    ("59 Co [ HEHe ]",      59,  None, "Co", "HEHe"),
    ("78 -> 78 Se [He]",    78,  78,   "Se", "He"),       # QQQ, on-mass
    ("31 -> 47 P [O2]",     31,  47,   "P",  "O2"),       # QQQ, mass shift
    ("51 ->  67 V [ NH3 ]", 51,  67,   "V",  "NH3"),
])
def test_label_shapes(label, mass, shift, element, mode):
    a = parse(label)
    assert (a.mass, a.mass_shift, a.element, a.mode) == (mass, shift, element, mode)
    assert a.label == label          # the export's own text is never rewritten


def test_msms_flag_and_key():
    assert parse("31 -> 47 P [O2]").is_msms
    assert not parse("75 As [He]").is_msms
    # the key distinguishes one element measured in two cell modes
    assert parse("52 Cr [He]").key != parse("52 Cr [No Gas]").key


def test_non_elements_are_not_invented():
    """A header that merely looks elemental must not name an element."""
    assert parse("220 Bkg").element is None        # 'Bk' is a truncated word
    assert parse("220 Xx").element is None         # well-formed, not an element
    assert parse("220 Xx").mass == 220             # the mass is still real


def test_unparseable_label_keeps_its_text():
    a = parse("Total Dissolved Solids")
    assert (a.mass, a.element, a.mode) == (None, None, None)
    assert a.label == "Total Dissolved Solids"


def test_triple_quad_export_reaches_the_element_keyed_checks(pass_csv, tmp_path):
    """The regression that matters is downstream, not in the regex.

    Rewriting the reference export's headers into MS/MS form must change nothing
    else: same parse, no warnings, and crm_recovery — which joins on element —
    still finds its material. Before mass_shift was understood, every element
    came back None and this check quietly evaluated nothing.
    """
    lines = pass_csv.read_text(encoding="utf-8").splitlines()
    lines[0] = re.sub(r"(\d+) ([A-Z][a-z]?)",
                      lambda m: f"{m.group(1)} -> {m.group(1)} {m.group(2)}", lines[0])
    qqq = tmp_path / "qqq.csv"
    qqq.write_text("\n".join(lines) + "\n", encoding="utf-8")

    batch = masshunter.parse(str(qqq))
    assert batch.warnings == []
    assert len(batch.analytes) == 8 and len(batch.istds) == 3
    assert all(a.is_msms and a.element for a in batch.analytes + batch.istds)
    assert checks.crm_recovery(batch, {"window_pct": [80, 120]}).outcome == Outcome.PASS
