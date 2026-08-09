"""Thermo Element 2 / Element XR ASCII export (.ASC) → Batch.

Why this is a separate parser rather than another template
----------------------------------------------------------
The MassHunter family puts one sample per row and one analyte per column. The
Element writes the transpose: one *isotope* per row, and each sample occupies a
block of columns::

                    blank            10 ppb 71A         100 ppb 71A
    Isotope   Conc AVG  Conc STD  Conc AVG  Conc STD  Conc AVG  Conc STD  …
              [cps]     [%]       [cps]     [%]       [cps]     [%]
    Na23(LR)  1258939.7 0.82      2648868.3 0.63      33962178.5 1.01
    Au197(LR) 328.8     11.96     69.2      15.79     530.9      3.53
    S32(MR)   35254.0   3.92      46588.7   3.38      217511.2   1.34

No column-pattern template can express that, because the thing a template maps —
a column — is a sample here, not a measurement. So the layout is read directly
and the canonical model is built the same way round as everywhere else.

Two details are load-bearing:

* **Isotopes read element-first**: ``Na23(LR)`` rather than ``23 Na [He]``. The
  parenthesised ``LR``/``MR``/``HR`` is the mass resolution used to separate an
  interference, which is what a collision cell does on a quadrupole — so it maps
  onto the same ``Analyte.mode`` field, and a report can compare like with like.
* **The Element states its own flags** in a per-sample column, exactly as
  MassHunter does in its first column. Those are carried, not dropped.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from icpms_qc.io import templates
from icpms_qc.model import Analyte, Batch, InstrumentFlag, Result, Sample, SampleType

#: "Na23(LR)", "Au197(LR)", "S32(MR)" — element, mass, resolution mode.
ISOTOPE_RE = re.compile(r"^\s*(?P<element>[A-Z][a-z]?)(?P<mass>\d+)\s*"
                        r"\(\s*(?P<mode>[LMH]R)\s*\)\s*$")

#: Row 3 in an untouched export, but located rather than assumed.
_ISOTOPE_HEADER = "isotope"

_CONC = "concentration avg"
_CONC_SD = "concentration std"
_INTENSITY = "intensity avg"
_RSD = "intensity rsd"
_FLAGS = "flags"


def analyte_from_isotope(text: str) -> Analyte | None:
    """`Na23(LR)` → Analyte(element=Na, mass=23, mode=LR), or None if not one."""
    m = ISOTOPE_RE.match(text)
    if not m:
        return None
    return Analyte(label=text.strip(), mass=int(m.group("mass")),
                   element=m.group("element"), mode=m.group("mode"))


def _num(raw: str | None) -> float | None:
    raw = (raw or "").strip()
    if not raw or raw in {"-", "n.a.", "N/A", "NA"}:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _read(path: str) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                return [r for r in csv.reader(fh, delimiter="\t")]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: could not decode with utf-8/cp1252/latin-1")


def looks_like_element_ascii(path: str) -> bool:
    """Cheap sniff, so a wrong file is refused rather than mis-parsed."""
    try:
        rows = _read(path)
    except (OSError, ValueError):
        return False
    return any(r and r[0].strip().lower() == _ISOTOPE_HEADER for r in rows[:12])


_META_TYPE = "analysis type"
_META_NAME = "sample name"
_META_FILE = "data file"
_META_DATE = "analysis date"
_META_DILUTION = "dilution factor"


def _metadata(rows: list[list[str]], upto: int) -> dict[str, str]:
    """`Label : <tab> <tab> value` lines above the isotope table."""
    out: dict[str, str] = {}
    for r in rows[:upto]:
        if not r or ":" not in (r[0] or ""):
            continue
        key = r[0].split(":")[0].strip().lower()
        value = next((c.strip() for c in r[1:] if c.strip()), "")
        if key:
            out[key] = value
    return out


def parse_sample_file(path: str, template: str | None = "element_ascii",
                      seq: int = 1) -> tuple[Sample, list[Analyte], dict[str, str]]:
    """One per-sample Element export: metadata block, then its isotope table.

    This layout states things MassHunter never does — the analysis type, the
    dilution factor, the sample amount and final volume, whether an internal
    standard was active, which quantification was applied. Those are exactly the
    inputs whose absence limits what can be verified on the Agilent side, so they
    are read rather than skipped.
    """
    rows = _read(path)
    header_i = next((i for i, r in enumerate(rows)
                     if r and r[0].strip().lower() == _ISOTOPE_HEADER), None)
    if header_i is None:
        raise ValueError(f"{path}: no 'Isotope' header row")
    meta = _metadata(rows, header_i)
    measures = [(c or "").strip().lower() for c in rows[header_i]]
    cols = {k: i for i, k in enumerate(measures) if k}

    tpl = templates.load(template) if template else None
    # The Element states the type outright; only when it does not do we fall back
    # to reading the name, which is the guessier path.
    stype = None
    if (raw_type := meta.get(_META_TYPE, "")) and tpl:
        stype = tpl.sample_type_vocab.get(raw_type)

    # "Sample Name :" is often blank because the run was identified by its data
    # file; that filename is the name the operator actually typed.
    name = meta.get(_META_NAME) or ""
    if not name:
        name = Path(meta.get(_META_FILE, path)).stem

    if stype is None and tpl:
        for pat, t in tpl.sample_type_patterns:
            if pat.search(name):
                stype = t
                break
    if stype is None and tpl and tpl.default_sample_type:
        stype = tpl.default_sample_type

    sample = Sample(name=name, seq_index=seq, type=stype or SampleType.OTHER)
    if tpl and tpl.expected_conc_from_name and sample.type in {
            SampleType.CAL_STD, SampleType.ICV, SampleType.CCV, SampleType.LCS}:
        if m := tpl.expected_conc_from_name.search(name):
            sample.level = float(m.group("value"))
    sample.dilution_factor = _num(meta.get(_META_DILUTION))

    analytes: list[Analyte] = []
    for r in rows[header_i + 1:]:
        if not r or not r[0].strip():
            continue
        a = analyte_from_isotope(r[0])
        if a is None:
            continue
        analytes.append(a)

        def cell(key: str) -> str | None:
            i = cols.get(key)
            return r[i] if i is not None and i < len(r) else None

        sample.results[a.label] = Result(
            conc=_num(cell(_CONC)), unit="ppb",
            intensity=_num(cell(_INTENSITY)), rsd_pct=_num(cell(_RSD)))
        if (flag := (cell("error") or "").strip()) and flag != "-":
            sample.flags.append(f"{a.label}: {flag}")
            sample.instrument_flags.append(
                InstrumentFlag(analyte=a.label, metric=flag, text=f"{a.label}: {flag}"))
    return sample, analytes, meta


def parse_folder(folder: str, template: str | None = "element_ascii") -> Batch:
    """Combine a directory of per-sample Element exports into one batch.

    The Element writes a file per acquisition, so a run is a folder rather than a
    file. They are ordered by the analysis timestamp they each carry, which is
    the sequence the samples were actually measured in — and sequence order is
    what the frequency and drift checks reason about.
    """
    paths = sorted(p for p in Path(folder).iterdir()
                   if p.suffix.lower() == ".asc" and p.is_file())
    if not paths:
        raise ValueError(f"{folder}: no .ASC files")

    parsed = []
    for p in paths:
        try:
            parsed.append((str(p), *parse_sample_file(str(p), template)))
        except ValueError:
            continue
    if not parsed:
        raise ValueError(f"{folder}: no per-sample Element exports could be read")

    def when(item):
        raw = item[3].get(_META_DATE, "")
        for fmt in ("%a, %d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return datetime.max                      # undated files sort last, not first
    parsed.sort(key=when)

    tpl = templates.load(template) if template else None
    batch = Batch(source_path=str(folder), template_id=tpl.id if tpl else "element_ascii",
                  instrument_family="thermo-element")
    seen: dict[str, Analyte] = {}
    unknown: set[str] = set()
    for seq, (path, sample, analytes, meta) in enumerate(parsed, start=1):
        sample.seq_index = seq
        for a in analytes:
            seen.setdefault(a.label, a)
        if sample.type is SampleType.OTHER and sample.name not in unknown:
            unknown.add(sample.name)
            batch.warnings.append(
                f"unrecognized analysis type '{meta.get(_META_TYPE, '')}' / name "
                f"'{sample.name}' -> OTHER (add it to the template)")
        batch.samples.append(sample)
    batch.analytes = list(seen.values())
    batch.flags_column_mapped = True             # the Element always writes Error/Flags
    return batch


def parse(path: str, template: str | None = "element_ascii") -> Batch:
    """Read an Element ASCII export into the canonical model.

    `template` supplies only the vocabulary a layout cannot: which sample names
    mean a standard or a blank, and where a nominal concentration hides in a
    name. Anything it does not claim stays `OTHER` with a warning, as everywhere
    else — the geometry is read, the meaning is declared.
    """
    rows = _read(path)
    tpl = templates.load(template) if template else None

    header_i = next((i for i, r in enumerate(rows)
                     if r and r[0].strip().lower() == _ISOTOPE_HEADER), None)
    if header_i is None:
        raise ValueError(f"{path}: not an Element ASCII export — no 'Isotope' header row")
    measures = rows[header_i]
    names_row = rows[0] if header_i else []

    # Each sample owns the columns from where its name appears up to the next
    # name. Deriving the block width instead of assuming it keeps this working
    # when a method exports a different set of measures.
    starts = [i for i, c in enumerate(names_row) if i and c.strip()]
    if not starts:
        raise ValueError(f"{path}: no sample names on the first row")
    bounds = list(zip(starts, starts[1:] + [max(len(measures), len(names_row))]))

    batch = Batch(source_path=str(path), template_id=tpl.id if tpl else "element_ascii",
                  instrument_family="thermo-element")
    batch.flags_column_mapped = any(
        (c or "").strip().lower() == _FLAGS for c in measures)

    analytes: dict[str, Analyte] = {}
    data_rows = []
    for r in rows[header_i + 1:]:
        if not r or not r[0].strip():
            continue
        a = analyte_from_isotope(r[0])
        if a is None:
            continue                       # unit row, blank row, trailing notes
        analytes[a.label] = a
        data_rows.append((a.label, r))
    if not data_rows:
        raise ValueError(f"{path}: no isotope rows below the header")
    batch.analytes = list(analytes.values())

    unknown: set[str] = set()
    for seq, (lo, hi) in enumerate(bounds, start=1):
        name = names_row[lo].strip()
        # Locate this sample's measures by name, not by offset: an export that
        # omits Concentration still puts Intensity where it says it does.
        cols: dict[str, int] = {}
        for i in range(lo, min(hi, len(measures))):
            key = (measures[i] or "").strip().lower()
            if key and key not in cols:
                cols[key] = i

        stype = tpl.sample_type_vocab.get(name) if tpl else None
        if stype is None and tpl:
            for pat, t in getattr(tpl, "sample_type_patterns", []):
                if pat.search(name):
                    stype = t
                    break
        if stype is None and tpl and tpl.default_sample_type:
            stype = tpl.default_sample_type
        if stype is None:
            stype = SampleType.OTHER
            if name not in unknown:
                unknown.add(name)
                batch.warnings.append(
                    f"unrecognized sample name '{name}' -> OTHER "
                    f"(add it to the template's sample_type_vocab or _patterns)")

        sample = Sample(name=name, seq_index=seq, type=stype)
        if tpl and tpl.expected_conc_from_name and stype in {
                SampleType.CAL_STD, SampleType.ICV, SampleType.CCV, SampleType.LCS}:
            if m := tpl.expected_conc_from_name.search(name):
                sample.level = float(m.group("value"))

        for label, row in data_rows:
            def cell(key: str) -> str | None:
                i = cols.get(key)
                return row[i] if i is not None and i < len(row) else None

            sample.results[label] = Result(
                conc=_num(cell(_CONC)),
                unit="ppb",
                intensity=_num(cell(_INTENSITY)),
                rsd_pct=_num(cell(_RSD)),
            )
            if (flag := (cell(_FLAGS) or "").strip()) and flag != "-":
                sample.flags.append(f"{label}: {flag}")
                sample.instrument_flags.append(
                    InstrumentFlag(analyte=label, metric=flag, text=f"{label}: {flag}"))

        batch.samples.append(sample)

    return batch
