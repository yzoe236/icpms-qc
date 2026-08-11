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
    assert a.label == f"{element} {nm:g}"      # unit never enters the label


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
