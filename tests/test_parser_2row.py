"""Two-row-header layout family (real-world MassHunter exports): synthetic fixture."""
from icpms_qc.io import masshunter
from icpms_qc.model import SampleType

CSV_2ROW = (
    "Sample,,,,,,,56  Fe  [ He ] ,,115  In ( ISTD )  [ No Gas ] ,\n"
    ",Rjct,Acq. Date-Time,Type,Level,Sample Name,Comment,Conc.,Conc. RSD,Conc. [ ppb ],Conc. RSD\n"
    "flagged: CPS RSD high,False,01-Jan-26 10:00:00,CalBlk,1,0,,0.001,5.0,100.0,1.0\n"
    ",False,01-Jan-26 10:05:00,CalStd,2,10ppb STD,,9.98,1.0,101.0,1.0\n"
    ",False,01-Jan-26 10:10:00,QC3,,10ppb STD,,10.1,1.0,99.0,1.0\n"
    ",False,01-Jan-26 10:15:00,BlkVrfy,,rinse,,0.002,9.9,98.0,1.0\n"
    ",False,01-Jan-26 10:20:00,Sample,,S1,,5.5,2.0,97.0,1.0\n"
)


def test_two_row_header_layout(tmp_path):
    p = tmp_path / "real2row.csv"
    p.write_text(CSV_2ROW, encoding="cp1252")
    batch = masshunter.parse(str(p), template="masshunter_conc_2row")

    assert [a.label for a in batch.analytes] == ["56  Fe  [ He ]"]
    assert [a.label for a in batch.istds] == ["115  In ( ISTD )  [ No Gas ]"]
    assert batch.warnings == []                       # everything mapped or ignored

    calstd = batch.find_sample("10ppb STD", SampleType.CAL_STD)
    assert calstd is not None
    assert calstd.level == 10.0                       # from the name, not the index column

    ccv = batch.of_type(SampleType.CCV)[0]            # QC3 -> CCV
    assert ccv.name == "10ppb STD" and ccv.level == 10.0
    assert batch.of_type(SampleType.CCB)[0].name == "rinse"

    fe = "56  Fe  [ He ]"
    assert batch.find_sample("S1").results[fe].conc == 5.5
    assert batch.samples[0].flags and "CPS RSD high" in batch.samples[0].flags[0]
    assert batch.samples[0].istd_intensities["115  In ( ISTD )  [ No Gas ]"] == 100.0


def test_two_row_ansi_encoding_fallback(tmp_path):
    text = CSV_2ROW.replace("flagged: CPS RSD high", "flagged: RSD ’high’")
    p = tmp_path / "ansi.csv"
    p.write_bytes(text.encode("cp1252"))
    batch = masshunter.parse(str(p), template="masshunter_conc_2row")
    assert len(batch.samples) == 5                    # cp1252 curly quotes handled


# ── regressions found by sweeping 165 real exports (2026-08-08) ──────────────

def test_utf8_bom_wins_over_the_templates_declared_encoding(tmp_path, pass_csv):
    """cp1252 decodes a BOM without raising, gluing 'ï»¿' to the first header.

    The whole sample-info block then matches nothing and the run reports empty —
    silently. One real export in the corpus was in exactly this state.
    """
    import csv as _csv
    from icpms_qc.io import masshunter

    rows = list(_csv.reader(open(pass_csv, newline="", encoding="utf-8")))
    src = tmp_path / "bom.csv"
    with open(src, "w", newline="", encoding="utf-8-sig") as fh:   # BOM written
        _csv.writer(fh).writerows(rows)

    raw, note = masshunter._read_raw(str(src), "cp1252")           # template lies
    assert raw[0][0] == "Seq"                                      # not "ï»¿Seq"
    assert note and "UTF-8 BOM" in note                            # and it says so


def test_pandas_unnamed_placeholders_read_as_blank():
    """A file round-tripped through pandas writes 'Unnamed: 4' for a blank cell."""
    from icpms_qc.io import masshunter, templates

    tpl = templates.load("masshunter_counts_2row")
    raw = [["Sample", "Unnamed: 1", "Unnamed: 2", "9  Be  [ No Gas ]", ""],
           ["", "Rjct", "Sample Name", "CPS", "CPS RSD"],
           ["", "False", "blank", "12", "3.1"]]
    names, body = masshunter._logical_header(raw, tpl)
    assert "Sample Name" in names          # not "Unnamed: 2 :: Sample Name"
    assert "Rjct" in names
