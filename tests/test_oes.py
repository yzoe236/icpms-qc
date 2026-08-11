"""Agilent ICP Expert workbooks (ICP-OES).

Optical emission needs its own reader for three reasons: it is a workbook with
one sheet per measure, an analyte is a wavelength rather than a mass, and the
same element is routinely measured on several lines at once.
"""
import pytest

openpyxl = pytest.importorskip("openpyxl")

from icpms_qc.io import oes
from icpms_qc.model import SampleType


@pytest.mark.parametrize("text,element,nm", [
    ("U 385.958", "U", 385.958),
    ("U 385.958 (cps)", "U", 385.958),
    ("U 385.958\n(mg/L)", "U", 385.958),
    ("Fe 259.940", "Fe", 259.940),
])
def test_an_analyte_is_a_wavelength_not_a_mass(text, element, nm):
    a = oes.analyte_from_line(text)
    assert (a.element, a.wavelength_nm, a.mass) == (element, nm, None)
    # The label keeps the wavelength exactly as the export wrote it, trailing
    # zero and all, as every label in this project does. Only the unit is
    # stripped, because it describes the measure rather than the analyte.
    assert a.label == text.split("(")[0].strip().replace("\n", " ")


def test_units_are_read_from_the_header_not_assumed():
    assert oes.unit_from_line("U 385.958 (mg/L)") == "mg/L"
    assert oes.unit_from_line("U 385.958 (cps)") is None      # cps is not a unit
    assert oes.unit_from_line("Sample Id") is None


def test_mass_spectrometry_labels_are_not_emission_lines():
    for text in ("238 U [No Gas]", "Sample Id", "Acquisition Time", ""):
        assert oes.analyte_from_line(text) is None


def _workbook(tmp_path, conc_filled=True):
    wb = openpyxl.Workbook()
    def sheet(title, unit, values):
        ws = wb.create_sheet(title)
        ws.append(["", "Sample Id", "Acquisition Time",
                   f"U 385.958\n({unit})", f"U 367.007\n({unit})"])
        for i, (name, a, b) in enumerate(values, start=1):
            ws.append([i, name, "7/27/2026", a, b])
        return ws
    rows = [("HNO3 blank", None, None), ("100 ppb U", 101.0, 99.0),
            ("250 ppb U", 248.0, 252.0), ("unknown-1", 57.0, 41.0)]
    sheet("Conc. in Sample Units", "mg/L",
          rows if conc_filled else [(n, None, None) for n, _, _ in rows])
    sheet("Corrected Intensities", "cps",
          [(n, 2000.0, 2100.0) for n, _, _ in rows])
    sheet("Corrected Intensities RSDs", "%",
          [(n, 1.2, 30.0) for n, _, _ in rows])
    del wb["Sheet"]
    p = tmp_path / "oes.xlsx"
    wb.save(p)
    return p


def test_sheets_are_merged_into_one_batch(tmp_path):
    b = oes.parse(str(_workbook(tmp_path)))
    assert b.instrument_family == "agilent-icp-oes"
    assert [a.label for a in b.analytes] == ["U 385.958", "U 367.007"]
    r = b.samples[1].results["U 385.958"]
    assert (r.conc, r.intensity, r.rsd_pct, r.unit) == (101.0, 2000.0, 1.2, "mg/L")


def test_sample_types_come_from_the_name_since_none_is_exported(tmp_path):
    b = oes.parse(str(_workbook(tmp_path)))
    got = {s.name: (s.type, s.level) for s in b.samples}
    assert got["HNO3 blank"][0] is SampleType.CAL_BLANK
    assert got["100 ppb U"] == (SampleType.CAL_STD, 100.0)
    assert got["unknown-1"][0] is SampleType.SAMPLE
    assert b.warnings == []


def test_an_unquantified_run_falls_back_to_its_intensities(tmp_path):
    """A workbook can carry a concentration sheet that was never filled in.

    The real Sattar run is exactly this: acquired but not quantified into sample
    units. Reporting a batch with no results at all would be worse than useless.
    """
    b = oes.parse(str(_workbook(tmp_path, conc_filled=False)))
    r = b.samples[1].results["U 385.958"]
    assert r.conc is None
    assert r.intensity == 2000.0        # the signal is still there to check
    assert r.rsd_pct == 1.2


def test_a_workbook_that_is_not_icp_expert_is_refused(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["something", "else"])
    p = tmp_path / "other.xlsx"
    wb.save(p)
    assert not oes.looks_like_oes_workbook(str(p))
    with pytest.raises(ValueError, match="not an ICP Expert workbook"):
        oes.parse(str(p))


# ── line agreement, the check optical emission needs and mass spec does not ──

def _batch_with_lines(pairs):
    """pairs: list of (conc_on_line_A, conc_on_line_B) per sample."""
    from icpms_qc.model import Analyte, Batch, Result, Sample, SampleType
    b = Batch(source_path="x", template_id="agilent_oes",
              instrument_family="agilent-icp-oes")
    b.analytes = [Analyte(label="Cr 205.560", element="Cr", wavelength_nm=205.560),
                  Analyte(label="Cr 267.716", element="Cr", wavelength_nm=267.716)]
    for i, (a, c) in enumerate(pairs, start=1):
        s = Sample(name=f"S{i}", seq_index=i, type=SampleType.SAMPLE)
        s.results = {"Cr 205.560": Result(conc=a, unit="mg/L"),
                     "Cr 267.716": Result(conc=c, unit="mg/L")}
        b.samples.append(s)
    return b


P = {"max_relative_diff": 0.25, "min_conc_x_loq": 10,
     "systematic_share": 0.8, "loq_ppb": {"default": 0.1}}


def test_lines_that_agree_pass():
    from icpms_qc.qc import checks
    from icpms_qc.qc.checks import Outcome
    res = checks.oes_line_agreement(
        _batch_with_lines([(10.0, 10.4), (20.0, 19.2), (30.0, 31.0)]), P)
    assert res.outcome == Outcome.PASS


def test_one_line_always_high_is_named_an_interference():
    """The real case this was built from: Fe emission riding on Cr 267.716.

    Both lines calibrate cleanly and still disagree, and the gap is one-sided.
    That is an overlap, not imprecision, and the report has to say which.
    """
    from icpms_qc.qc import checks
    from icpms_qc.qc.checks import Outcome
    res = checks.oes_line_agreement(
        _batch_with_lines([(10.0, 14.0), (20.0, 29.0), (30.0, 44.0), (40.0, 57.0)]), P)
    assert res.outcome == Outcome.FAIL
    row = next(d for d in res.details if d["ok"] is False)
    assert "Cr 267.716 reads higher" in row["note"]
    assert "interference" in row["note"]


def test_two_sided_scatter_is_not_called_an_interference():
    from icpms_qc.qc import checks
    res = checks.oes_line_agreement(
        _batch_with_lines([(10.0, 14.0), (20.0, 14.0), (30.0, 44.0), (40.0, 28.0)]), P)
    row = next(d for d in res.details if d["ok"] is False)
    assert "scatter rather than an interference" in row["note"]


def test_a_single_line_element_has_nothing_to_compare():
    from icpms_qc.qc import checks
    from icpms_qc.qc.checks import Outcome
    b = _batch_with_lines([(10.0, 10.0)])
    b.analytes = b.analytes[:1]
    assert checks.oes_line_agreement(b, P).outcome == Outcome.NOT_EVALUATED
