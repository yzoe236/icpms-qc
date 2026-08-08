"""Certified reference materials: expected values that the export cannot carry.

A CRM certifies dozens of elements at dozens of different values, so it cannot
be expressed as the single Level column every other recovery check divides by.
The values come from the certificate library in configs/crm/.
"""
import pytest

from icpms_qc.io import masshunter
from icpms_qc.qc import checks, crm
from icpms_qc.qc.checks import Outcome

CD = "111 Cd Conc. [ppb]"
WINDOW = {"window_pct": [80, 120]}


# ── unit handling ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,frm,to,expected", [
    (1.0, "ppb", "ppb", 1.0),
    (1.0, "ppm", "ppb", 1000.0),
    (1000.0, "ppb", "ppm", 1.0),
    (1.0, "mg/L", "ug/L", 1000.0),
    (1.0, "µg/L", "ppb", 1.0),        # U+00B5 micro sign
    (1.0, "μg/L", "ppb", 1.0),        # U+03BC greek mu
    (1.0, "ng/g", "ppb", 1.0),
    (0.5, "%", "ppm", 5000.0),
])
def test_unit_conversion(value, frm, to, expected):
    assert crm.convert(value, frm, to) == pytest.approx(expected)


def test_unknown_unit_refuses_rather_than_guesses():
    assert crm.convert(1.0, "fathoms", "ppb") is None
    assert crm.convert(1.0, "ppb", "") is None


# ── library ──────────────────────────────────────────────────────────────────

def test_shipped_library_loads():
    library = crm.load_library("crm")
    assert library, "configs/crm should ship at least the synthetic example"
    example = next(c for c in library if c.id == "example_synthetic_water")
    assert example.unit == "ppb"
    assert example.certified["Cd"].value == 10.0
    assert example.certified["Cd"].uncertainty == 0.3
    assert example.matches("CRM-EXAMPLE-1")
    assert not example.matches("S001")


def test_crm_without_certified_values_is_rejected(tmp_path):
    (tmp_path / "broken.yaml").write_text("id: broken\nunit: ppb\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no usable values"):
        crm.load_library(str(tmp_path))


def test_crm_without_unit_is_rejected(tmp_path):
    (tmp_path / "broken.yaml").write_text(
        "id: broken\ncertified: {Cd: 10.0}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must declare a 'unit'"):
        crm.load_library(str(tmp_path))


# ── the check ────────────────────────────────────────────────────────────────

def test_crm_in_the_reference_batch_recovers(pass_csv):
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)), WINDOW)
    assert res.outcome == Outcome.PASS
    rows = [d for d in res.details if d["analyte"] == "111 Cd"]
    assert len(rows) == 1
    assert rows[0]["reference"] == 10.0
    assert rows[0]["recovery_pct"] == pytest.approx(100, abs=10)
    # the source's own uncertainty is reported, but never decides
    assert "within_ref_uncert" in rows[0]


def test_a_certified_element_out_of_window_fails(pass_csv, edited_csv):
    batch = masshunter.parse(str(edited_csv(pass_csv, {("CRM-EXAMPLE-1", CD): "50.0"})))
    res = checks.crm_recovery(batch, WINDOW)
    assert res.outcome == Outcome.FAIL
    bad = [d for d in res.details if d["ok"] is False]
    assert [d["analyte"] for d in bad] == ["111 Cd"]
    assert bad[0]["recovery_pct"] == pytest.approx(500, abs=1)


def test_no_matching_sample_is_loud_not_silent(pass_csv, edited_csv):
    """A batch with no CRM must say so, not report a quiet pass."""
    batch = masshunter.parse(
        str(edited_csv(pass_csv, {("CRM-EXAMPLE-1", "Sample Name"): "S099"})))
    res = checks.crm_recovery(batch, WINDOW)
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "no sample name matched" in res.reason


def test_unconvertible_certificate_unit_is_reported(pass_csv, tmp_path):
    (tmp_path / "odd.yaml").write_text(
        "id: odd_units\nunit: fathoms\n"
        "match: {name_patterns: ['CRM-EXAMPLE-1']}\n"
        "certified: {Cd: 10.0}\n", encoding="utf-8")
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)),
                              {**WINDOW, "library": str(tmp_path)})
    row = next(d for d in res.details if d["analyte"] == "111 Cd")
    assert row["ok"] is None
    assert "cannot convert" in row["note"]


def _library(tmp_path, body: str):
    (tmp_path / "m.yaml").write_text(
        "id: m\nunit: ppb\nmatch: {name_patterns: ['CRM-EXAMPLE-1']}\n" + body,
        encoding="utf-8")
    return {**WINDOW, "library": str(tmp_path)}


def test_information_values_are_reported_but_never_fail(pass_csv, tmp_path):
    """A compilation's information value can rest on one lab. It cannot fail a batch."""
    params = _library(tmp_path, "certified:\n"
                                "  Cd: {value: 1.0, type: information}\n")
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)), params)
    row = next(d for d in res.details if d["analyte"] == "111 Cd")
    assert row["recovery_pct"] == pytest.approx(1000, abs=100)   # wildly off
    assert row["ok"] is None                                     # and still not a failure
    assert row["value_type"] == "information"
    assert "not decisive" in row["note"]
    assert res.outcome != Outcome.FAIL


def test_reference_values_do_decide(pass_csv, tmp_path):
    """`reference` (a GeoReM preferred value) carries the same weight as certified."""
    params = _library(tmp_path, "certified:\n"
                                "  Cd: {value: 1.0, type: reference}\n")
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)), params)
    row = next(d for d in res.details if d["analyte"] == "111 Cd")
    assert row["ok"] is False and row["value_type"] == "reference"
    assert res.outcome == Outcome.FAIL


def test_default_value_type_applies_to_bare_entries(pass_csv, tmp_path):
    params = _library(tmp_path, "default_value_type: reference\n"
                                "certified:\n  Cd: 10.0\n")
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)), params)
    row = next(d for d in res.details if d["analyte"] == "111 Cd")
    assert row["value_type"] == "reference"


def test_unknown_value_type_is_rejected(tmp_path):
    (tmp_path / "m.yaml").write_text(
        "id: m\nunit: ppb\ncertified: {Cd: {value: 1.0, type: rumour}}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="unknown value type"):
        crm.load_library(str(tmp_path))


def test_unfilled_placeholders_are_skipped_and_announced(pass_csv, tmp_path):
    """A half-transcribed library is normal; a silent one would be a lie."""
    params = _library(tmp_path, "certified:\n"
                                "  Cd: {value: 10.0}\n"
                                "  Pb: {value: }\n"
                                "  Zn:\n")
    library = crm.load_library(str(tmp_path))
    assert set(library[0].unfilled) == {"Pb", "Zn"}
    assert set(library[0].certified) == {"Cd"}

    res = checks.crm_recovery(masshunter.parse(str(pass_csv)), params)
    note = next(d for d in res.details if d["analyte"] == "-")["note"]
    assert "2 element(s)" in note and "Pb" in note and "Zn" in note
    # Pb was measured in this batch but has no value — it must not look checked
    assert not [d for d in res.details if d["analyte"] == "208 Pb"]


def test_provenance_is_captured(tmp_path):
    (tmp_path / "m.yaml").write_text(
        "id: m\nunit: ppm\ncertified: {Cd: 1.0}\n"
        "provenance:\n  compilation: GeoReM\n  version: '2024-01'\n"
        "  accessed: '2026-08-03'\n", encoding="utf-8")
    p = crm.load_library(str(tmp_path))[0].provenance
    assert p.compilation == "GeoReM"
    assert "GeoReM" in p.describe() and "2026-08-03" in p.describe()


def test_missing_library_is_not_evaluated(pass_csv, tmp_path):
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)),
                              {**WINDOW, "library": str(tmp_path / "nope")})
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "unavailable" in res.reason


def test_empty_library_is_not_evaluated(pass_csv, tmp_path):
    res = checks.crm_recovery(masshunter.parse(str(pass_csv)),
                              {**WINDOW, "library": str(tmp_path)})
    assert res.outcome == Outcome.NOT_EVALUATED
    assert "empty" in res.reason
