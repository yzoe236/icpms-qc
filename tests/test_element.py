"""Thermo Element 2 / Element XR ASCII exports.

The Element writes the transpose of a MassHunter export and in two different
shapes: a multi-sample summary table, and one file per acquisition with a
metadata block on top. Both occur in the same run folder.
"""
import pytest

from icpms_qc.io import element
from icpms_qc.model import SampleType

SUMMARY = "\t".join(["", "blank", "", "10 ppb 71A", ""]) + "\n\n\n" + \
    "\t".join(["Isotope", "Intensity AVG", "Intensity RSD",
               "Intensity AVG", "Intensity RSD"]) + "\n" + \
    "\t".join(["", "[cps]", "[%]", "[cps]", "[%]"]) + "\n\n" + \
    "\t".join(["Na23(LR)", "1258939.7", "0.82", "2648868.3", "0.63"]) + "\n" + \
    "\t".join(["S32(MR)", "35254.0", "3.92", "46588.7", "3.38"]) + "\n"

PER_SAMPLE = "\n".join([
    "Acquisition Parameters",
    "Data File :\t\tE:\\Data\\Citro\\10 ppb.dat",
    "Analysis Date :\t\tWed, 15-Jul-2026 16:11:40",
    "Sample Name :\t\t",
    "Evaluation Parameters",
    "Analysis Type :\t\tSTD",
    "Dilution Factor :\t\t5",
    "Int. Std. active :\t\tNo",
    "\t".join(["Isotope", "Error", "Intensity AVG", "Intensity RSD",
               "Concentration AVG"]),
    "\t".join(["", "", "[cps]", "[%]", ""]),
    "\t".join(["Cd111(LR)", "-", "430.1", "8.74", "9.8"]),
    "\t".join(["As75(HR)", "O", "0.1", "173.21", ""]),
]) + "\n"


def _write(tmp_path, text, name):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── isotope labels ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,element_,mass,mode", [
    ("Na23(LR)", "Na", 23, "LR"),
    ("Au197(LR)", "Au", 197, "LR"),
    ("S32(MR)", "S", 32, "MR"),
    ("As75(HR)", "As", 75, "HR"),
])
def test_isotope_reads_element_first(text, element_, mass, mode):
    """`Na23(LR)`, not `23 Na [He]` — and LR/MR/HR is the Element's mode."""
    a = element.analyte_from_isotope(text)
    assert (a.element, a.mass, a.mode, a.label) == (element_, mass, mode, text)


def test_non_isotope_rows_are_rejected():
    for junk in ("Isotope", "[cps]", "Flags:  S=Amplifier Skipped", ""):
        assert element.analyte_from_isotope(junk) is None


# ── the multi-sample summary ─────────────────────────────────────────────────

def test_summary_layout_is_read_transposed(tmp_path):
    """Columns are samples here, which no column-pattern template can express."""
    b = element.parse(str(_write(tmp_path, SUMMARY, "s.ASC")))
    assert [s.name for s in b.samples] == ["blank", "10 ppb 71A"]
    assert [a.label for a in b.analytes] == ["Na23(LR)", "S32(MR)"]
    assert b.samples[0].type is SampleType.CAL_BLANK
    assert b.samples[1].type is SampleType.CAL_STD
    assert b.samples[1].level == 10.0
    assert b.samples[1].results["Na23(LR)"].intensity == pytest.approx(2648868.3)
    assert b.samples[1].results["Na23(LR)"].rsd_pct == pytest.approx(0.63)


# ── the per-sample export ────────────────────────────────────────────────────

def test_per_sample_type_comes_from_the_export_not_the_name(tmp_path):
    """The Element states its Analysis Type outright — read it, don't infer."""
    s, analytes, meta = element.parse_sample_file(
        str(_write(tmp_path, PER_SAMPLE, "p.ASC")))
    assert meta["analysis type"] == "STD"
    assert s.type is SampleType.CAL_STD
    assert s.name == "10 ppb"            # Sample Name was blank; the data file has it
    assert s.level == 10.0
    assert [a.label for a in analytes] == ["Cd111(LR)", "As75(HR)"]


def test_the_fields_masshunter_never_exports_are_kept(tmp_path):
    """Dilution factor is the input whose absence limits the Agilent cross-check."""
    s, _, meta = element.parse_sample_file(str(_write(tmp_path, PER_SAMPLE, "p.ASC")))
    assert s.dilution_factor == 5.0
    assert meta["int. std. active"] == "No"


def test_the_instruments_own_error_flag_is_carried(tmp_path):
    s, _, _ = element.parse_sample_file(str(_write(tmp_path, PER_SAMPLE, "p.ASC")))
    flags = {f.analyte: f.metric for f in s.instrument_flags}
    assert flags == {"As75(HR)": "O"}          # 'O' = Overflow; '-' is not a flag


def test_a_folder_becomes_one_batch_in_acquisition_order(tmp_path):
    """A run is a folder here, and the timestamps are the sequence."""
    _write(tmp_path, PER_SAMPLE, "b.ASC")
    later = PER_SAMPLE.replace("16:11:40", "16:40:00").replace("10 ppb.dat", "blank.dat") \
                      .replace("Analysis Type :\t\tSTD", "Analysis Type :\t\tBLK")
    _write(tmp_path, later, "a.ASC")

    b = element.parse_folder(str(tmp_path))
    assert [s.name for s in b.samples] == ["10 ppb", "blank"]   # by time, not filename
    assert [s.seq_index for s in b.samples] == [1, 2]
    assert b.samples[1].type is SampleType.CAL_BLANK
    assert b.instrument_family == "thermo-element"


def test_a_file_without_an_isotope_table_is_refused(tmp_path):
    p = _write(tmp_path, "Acquisition Parameters\nData File :\t\tx.dat\n", "n.ASC")
    assert not element.looks_like_element_ascii(str(p))
    with pytest.raises(ValueError, match="Isotope"):
        element.parse_sample_file(str(p))
