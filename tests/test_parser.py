"""Parser: reference layout → canonical model, faithfully and loudly."""
from icpqc.io import masshunter
from icpqc.model import SampleType


def test_parse_reference_layout(pass_csv):
    batch = masshunter.parse(str(pass_csv))
    assert batch.template_id == "masshunter_quant_wide"
    assert batch.instrument_family == "agilent-masshunter"
    assert len(batch.samples) == 32
    assert len(batch.analytes) == 8
    assert len(batch.istds) == 3
    assert batch.warnings == []          # every column and type recognized

    assert len(batch.of_type(SampleType.CAL_STD)) == 5
    assert len(batch.of_type(SampleType.CCV)) == 2
    assert len(batch.of_type(SampleType.SAMPLE)) == 14

    dup = batch.of_type(SampleType.DUP)[0]
    assert dup.name == "S003-DUP"
    assert dup.parent_name == "S003"

    ms = batch.of_type(SampleType.MS)[0]
    assert ms.parent_name == "S005"
    assert ms.level == 25.0

    s1 = batch.find_sample("S001", SampleType.SAMPLE)
    assert s1 is not None
    assert len(s1.results) == 8
    assert len(s1.istd_intensities) == 3
    assert all(r.conc is not None for r in s1.results.values())

    # analyte label parsing
    as75 = next(a for a in batch.analytes if a.label == "75 As [He]")
    assert as75.mass == 75 and as75.element == "As"


def test_unknown_sample_type_is_loud(tmp_path, pass_csv):
    text = pass_csv.read_text(encoding="utf-8").replace(",MB,", ",PrepBlank,", 1)
    weird = tmp_path / "weird.csv"
    weird.write_text(text, encoding="utf-8")
    batch = masshunter.parse(str(weird))
    assert any("PrepBlank" in w for w in batch.warnings)
    assert len(batch.of_type(SampleType.OTHER)) == 1
