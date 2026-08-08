"""Fingerprinting, redaction, and draft validation.

No model is called here: the parts that must never regress — what leaves the
machine, and whether a draft is honestly reported — are pure functions.
"""
from __future__ import annotations

import csv

import pytest

from icpms_qc.io import mapper

VENDOR_B_HEADER = [
    "No.", "Sample Id", "Sample Type", "Std Conc (ppb)",
    "Be 9 [ KED ] Concentration (ug/L)", "Be 9 [ KED ] Intensity (cps)",
    "Pb 208 [ STD ] Concentration (ug/L)", "Pb 208 [ STD ] Intensity (cps)",
    "Sc 45 [ KED ] ISTD Intensity (cps)", "Acquisition Time",
]
VENDOR_B_ROWS = [
    ["1", "Calibration Blank", "CalBlank", "", "0.0001", "1200", "0.0002", "1300", "251000", "14:00"],
    ["2", "Standard 1", "Standard", "10", "10.02", "91200", "9.98", "91000", "250400", "14:01"],
    ["3", "Standard 2", "Standard", "50", "49.7", "448000", "50.3", "453000", "249800", "14:02"],
    ["4", "Standard 3", "Standard", "100", "100.4", "905000", "99.6", "897000", "250100", "14:03"],
    ["5", "CCV 50ppb", "QC Check", "50", "49.1", "443000", "51.2", "461000", "248900", "14:04"],
    ["6", "Riverside Plant 3", "Unknown", "", "2.11", "20200", "7.44", "68000", "247500", "14:05"],
]

VENDOR_B_TEMPLATE = """\
id: vendor_b
instrument_family: thermo-qtegra
columns:
  seq: "No."
  sample_name: "Sample Id"
  sample_type: "Sample Type"
  level: "Std Conc (ppb)"
analyte_conc_pattern: '^(?P<label>[A-Za-z]{1,2}\\s+\\d{1,3}\\s*\\[[^\\]]+\\])\\s*Concentration\\s*\\((?P<unit>[^)]+)\\)$'
analyte_cps_pattern: '^(?P<label>[A-Za-z]{1,2}\\s+\\d{1,3}\\s*\\[[^\\]]+\\])\\s*Intensity\\s*\\(cps\\)$'
istd_cps_pattern: '^(?P<label>[A-Za-z]{1,2}\\s+\\d{1,3}\\s*\\[[^\\]]+\\])\\s*ISTD\\s+Intensity\\s*\\(cps\\)$'
ignore_patterns:
  - '^Acquisition Time$'
sample_type_vocab:
  CalBlank: CAL_BLANK
  Standard: CAL_STD
  "QC Check": CCV
  Unknown: SAMPLE
"""


@pytest.fixture
def vendor_b(tmp_path):
    p = tmp_path / "vendor_b.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(VENDOR_B_HEADER)
        w.writerows(VENDOR_B_ROWS)
    return str(p)


# ── redaction: the privacy contract ──────────────────────────────────────────
@pytest.mark.parametrize("client_text", [
    "Riverside Plant 3",
    "Northside Well 7 (ACME Corp)",
    "Patient-4471 serum",
    "Confidential Site B",
])
def test_redaction_masks_client_identifiers(client_text):
    out = mapper.redact(client_text)
    assert mapper._MASK in out
    for token in client_text.split():
        if token.lower() not in mapper._QC_WORDS and not token[0].isdigit():
            assert token not in out, f"{token!r} leaked through redaction"


@pytest.mark.parametrize("lab_text", [
    "CCV 50ppb", "Method Blank", "Std 5", "Conc. [ppb]", "CPS (ISTD)",
    "9 Be Conc. [ppb]", "Sample Name", "Continuing Calib Blank",
])
def test_redaction_preserves_lab_vocabulary(lab_text):
    """A header row must survive intact, or the model goes blind to the layout."""
    assert mapper.redact(lab_text) == lab_text


def test_redaction_keeps_qc_suffix_on_masked_name():
    """The parent-link suffix is structure, not identity — it must survive."""
    assert mapper.redact("Riverside Plant 3 DUP").endswith("DUP")


def test_fingerprint_excludes_measurements(vendor_b):
    fp = mapper.fingerprint(vendor_b)
    blob = fp.to_json()
    for measurement in ("905000", "49.7", "251000", "20200"):
        assert measurement not in blob, f"measurement {measurement} leaked"


def test_fingerprint_masks_names_by_default_and_opts_in(vendor_b):
    assert "Riverside" not in mapper.fingerprint(vendor_b).to_json()
    assert "Riverside" in mapper.fingerprint(vendor_b, include_names=True).to_json()


# ── fingerprint content ──────────────────────────────────────────────────────
def test_fingerprint_reports_headers_and_type_vocabulary(vendor_b):
    fp = mapper.fingerprint(vendor_b)
    assert fp.n_columns == len(VENDOR_B_HEADER)
    assert fp.head_rows[0] == VENDOR_B_HEADER          # headers sent verbatim

    type_col = next(c for c in fp.columns if c.header == "Sample Type")
    assert type_col.kind == "categorical"
    assert set(type_col.values) == {"CalBlank", "Standard", "QC Check", "Unknown"}


def test_fingerprint_classifies_measurement_columns_as_numeric(vendor_b):
    fp = mapper.fingerprint(vendor_b)
    conc = next(c for c in fp.columns if "Concentration" in c.header)
    assert conc.kind == "numeric"
    assert not conc.values


# ── draft handling ───────────────────────────────────────────────────────────
def test_extract_yaml_survives_fences_and_preamble():
    assert mapper.extract_yaml(
        "Here you go:\n```yaml\nid: x\ncolumns: {}\n```\n").startswith("id: x")
    assert mapper.extract_yaml("Some preamble\nid: y\ncolumns: {}\n").startswith("id: y")


def test_validate_accepts_a_correct_draft(vendor_b):
    v = mapper.validate(VENDOR_B_TEMPLATE, vendor_b)
    assert v.ok, v.error
    assert v.n_samples == len(VENDOR_B_ROWS)
    assert len(v.analytes) == 2
    assert len(v.istds) == 1
    assert v.type_counts.get("CAL_STD") == 3
    assert not v.unmapped_columns and not v.unknown_types


def test_validate_rejects_a_draft_that_matches_nothing(vendor_b):
    broken = VENDOR_B_TEMPLATE.replace(
        "(?P<label>[A-Za-z]{1,2}\\s+\\d{1,3}\\s*\\[[^\\]]+\\])\\s*Concentration",
        "(?P<label>NOPE)")
    v = mapper.validate(broken.replace("analyte_cps_pattern", "unused_pattern"), vendor_b)
    assert not v.ok
    assert v.error


def test_validate_surfaces_unmapped_types_rather_than_swallowing_them(vendor_b):
    partial = VENDOR_B_TEMPLATE.replace('  "QC Check": CCV\n', "")
    v = mapper.validate(partial, vendor_b)
    assert v.ok
    assert v.type_counts.get("OTHER") == 1
    assert any("QC Check" in w for w in v.unknown_types)


def test_review_report_never_stays_silent_about_a_gap(vendor_b):
    partial = VENDOR_B_TEMPLATE.replace('  "QC Check": CCV\n', "")
    report = mapper.review_report(mapper.validate(partial, vendor_b),
                                  mapper.fingerprint(vendor_b))
    assert "OTHER" in report and "review the flagged items" in report

    clean = mapper.review_report(mapper.validate(VENDOR_B_TEMPLATE, vendor_b),
                                 mapper.fingerprint(vendor_b))
    assert "clean parse" in clean


def test_review_report_states_the_failure_when_a_draft_does_not_parse():
    v = mapper.Validation(False, "ValueError: no analyte columns matched")
    assert "REJECTED" in mapper.review_report(v, None)


# ── two-row headers: the shape that leaked measurements in the field ──────────
TWO_ROW_HEADER = [
    ["Sample", "", "", "", "", "56  Fe  [ No Gas ]", "", "59  Co  [ He ]", ""],
    ["", "Rjct", "Type", "Level", "Sample Name", "Conc.", "Conc. RSD", "Conc.", "Conc. RSD"],
]
# A real sequence opens with rinses and blanks: mostly empty or textual cells. An
# average over the first few rows reads as "not numeric" and hides the sub-header.
TWO_ROW_BODY = [
    ["115 In (ISTD): CPS RSD value 14.81 is above limit", "False", "", "", "rinse", "", "", "", ""],
    ["", "False", "", "", "rinse", "", "", "", ""],
    ["", "False", "CalBlk", "1", "calib blank 1", "0.000", "119.0", "0.000", "0.7"],
    ["", "False", "CalStd", "5", "1000ppb 71A", "0.822", "13.5", "0.031", "1.1"],
    ["", "False", "Sample", "", "Northside Well 4", "0.197", "14.4", "0.024", "0.8"],
]


@pytest.fixture
def two_row(tmp_path):
    p = tmp_path / "two_row.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerows(TWO_ROW_HEADER)
        w.writerows(TWO_ROW_BODY)
    return str(p)


def test_subheader_detected_despite_rinse_heavy_opening_rows(two_row):
    rows = TWO_ROW_HEADER + TWO_ROW_BODY
    assert mapper._looks_like_subheader(rows, len(TWO_ROW_HEADER[1]))


def test_two_row_header_does_not_leak_measurements(two_row):
    """Regression: a sub-header profiled as data made every measurement column look
    like free text, and free-text columns list their values."""
    blob = mapper.fingerprint(two_row).to_json()
    for measurement in ("0.822", "119.0", "0.031", "14.4", "0.197"):
        assert measurement not in blob, f"measurement {measurement} leaked"


def test_two_row_header_still_shows_the_model_what_it_needs(two_row):
    blob = mapper.fingerprint(two_row).to_json()
    for structural in ("Conc.", "Type", "Level", "Sample Name", "56  Fe  [ No Gas ]"):
        assert structural in blob, f"{structural!r} was masked; the model goes blind"


def test_instrument_qc_blob_is_masked_and_clipped(two_row):
    """Vendor QC warning text carries measured values inside prose."""
    blob = mapper.fingerprint(two_row).to_json()
    assert "14.81" not in blob


def test_long_values_are_clipped():
    assert len(mapper._clip("x" * 5000)) < mapper.MAX_VALUE_CHARS + 10


# ── resolution pass: minimum disclosure, and only of QC rows ─────────────────
RESOLVE_HEADER = ["No.", "Sample Id", "Sample Type", "Std Conc (ppb)",
                  "Be 9 [ KED ] Concentration (ug/L)"]
RESOLVE_ROWS = [
    ["1", "ICV 25ppb", "QC Check", "25", "24.8"],
    ["2", "CCV 50ppb", "QC Check", "50", "49.6"],
    ["3", "Northside Well 1", "Unknown", "", "3.1"],
    ["4", "Northside Well 2", "Unknown", "", "5.7"],
    ["5", "Northside Well 3", "Unknown", "", "2.2"],
    ["6", "Northside Well 4", "Unknown", "", "9.9"],
    ["7", "Northside Well 5", "Unknown", "", "1.4"],
]
RESOLVE_TEMPLATE = r"""id: r
instrument_family: unknown
columns:
  sample_name: "Sample Id"
  sample_type: "Sample Type"
analyte_conc_pattern: '^(?P<label>.+?) Concentration \((?P<unit>[^)]+)\)$'
sample_type_vocab: {}
"""


@pytest.fixture
def resolve_file(tmp_path):
    p = tmp_path / "r.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(RESOLVE_HEADER)
        w.writerows(RESOLVE_ROWS)
    return str(p)


def test_resolution_discloses_qc_rows_only(resolve_file):
    plan = mapper.plan_resolution(RESOLVE_TEMPLATE, resolve_file)
    assert plan.names_by_type["QC Check"] == ["ICV 25ppb", "CCV 50ppb"]
    assert "Unknown" not in plan.names_by_type, "client sample names were disclosed"
    assert "Unknown" in plan.skipped


def test_resolution_withholds_a_type_that_dominates_the_batch(resolve_file):
    """A type covering most rows is the samples, whatever it happens to be called."""
    plan = mapper.plan_resolution(RESOLVE_TEMPLATE, resolve_file)
    assert "too many to be QC" in plan.skipped["Unknown"]


def test_resolution_skips_types_already_mapped(resolve_file):
    mapped = RESOLVE_TEMPLATE.replace("sample_type_vocab: {}",
                                      'sample_type_vocab:\n  "QC Check": CCV')
    plan = mapper.plan_resolution(mapped, resolve_file)
    assert "QC Check" not in plan.names_by_type


def test_disclosure_can_be_shown_before_it_is_sent(resolve_file):
    described = mapper.plan_resolution(RESOLVE_TEMPLATE, resolve_file).describe()
    assert "ICV 25ppb" in described and "withheld" in described


def test_merge_fragment_adds_vocabulary_without_losing_the_template():
    merged = mapper.merge_fragment(
        RESOLVE_TEMPLATE,
        'sample_type_vocab:\n  "QC Check": CCV\nparent_rules:\n'
        '  - {type: DUP, name_suffix: "-DUP"}\n')
    data = __import__("yaml").safe_load(merged)
    assert data["sample_type_vocab"]["QC Check"] == "CCV"
    assert data["parent_rules"][0]["name_suffix"] == "-DUP"
    assert data["analyte_conc_pattern"], "the original template was damaged"
