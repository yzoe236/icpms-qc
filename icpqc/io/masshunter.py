"""Agilent MassHunter quantitative batch export (CSV) → Batch.

Template-driven (see icpqc.io.templates): nothing lab-specific is hardcoded.
Supports both the single-header reference layout and the real-world two-row
header layout (analyte labels spanning column pairs above measure sub-headers).
Unrecognized sample-type strings map to SampleType.OTHER and append a
Batch.warning — loud, never guessed (SPEC §3).
"""
from __future__ import annotations

import csv
import re

from icpqc.io import templates
from icpqc.model import Analyte, Batch, Result, Sample, SampleType

_LABEL_RE = re.compile(r"^(?P<mass>\d+)\s+\[?(?P<element>[A-Za-z]{1,2})\]?")

#: sample types whose expected conc may be recovered from the sample name
_NAME_LEVEL_TYPES = {SampleType.CAL_STD, SampleType.ICV, SampleType.CCV,
                     SampleType.LCS, SampleType.MS, SampleType.MSD}


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if raw in {"", "-", "N/A", "n/a", "NA"}:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _analyte_from_label(label: str) -> Analyte:
    m = _LABEL_RE.match(label)
    return Analyte(
        label=label,
        mass=int(m.group("mass")) if m else None,
        element=m.group("element") if m else None,
    )


def _read_raw(path: str, encoding: str) -> tuple[list[list[str]], str | None]:
    """Read all CSV rows; fall back to cp1252 (real MassHunter exports are ANSI)."""
    try:
        with open(path, newline="", encoding=encoding) as fh:
            return list(csv.reader(fh)), None
    except UnicodeDecodeError:
        with open(path, newline="", encoding="cp1252") as fh:
            return (list(csv.reader(fh)),
                    f"decoded with cp1252 fallback (template encoding '{encoding}' failed)")


def _logical_header(raw: list[list[str]], tpl: templates.Template) -> tuple[list[str], list[list[str]]]:
    """Collapse 1- or 2-row headers into one list of logical column names."""
    if tpl.header_rows == 1:
        return [c.strip() for c in raw[0]], raw[1:]
    h1, h2 = raw[0], raw[1]
    width = max(len(h1), len(h2))
    names, cur = [], ""
    for i in range(width):
        lab = (h1[i] if i < len(h1) else "").strip()
        sub = (h2[i] if i < len(h2) else "").strip()
        if lab:
            cur = lab
        if not sub:
            names.append(cur)                       # e.g. the unnamed flag column
        elif not cur or cur in tpl.header_group_labels:
            names.append(sub)                       # sample-info block columns
        else:
            names.append(f"{cur} :: {sub}")         # analyte-region columns
    return names, raw[2:]


def parse(export_csv: str, template: str = "masshunter_quant_wide") -> Batch:
    tpl = templates.load(template)
    raw, enc_warning = _read_raw(export_csv, tpl.encoding)
    if len(raw) <= tpl.header_rows:
        raise ValueError(f"{export_csv}: no data rows below the header")
    names, data_rows = _logical_header(raw, tpl)

    batch = Batch(source_path=str(export_csv), template_id=tpl.id,
                  instrument_family=tpl.instrument_family)
    if enc_warning:
        batch.warnings.append(enc_warning)

    fixed_cols = set(tpl.columns.values())
    conc_cols: dict[str, tuple[str, str]] = {}   # analyte label -> (column, unit)
    cps_cols: dict[str, str] = {}                # analyte label -> column
    istd_cols: dict[str, str] = {}               # istd label -> column (conc or cps)

    for col in names:
        if col in fixed_cols:
            continue
        if any(p.search(col) for p in tpl.ignore_patterns):
            continue
        if tpl.istd_label_pattern and tpl.istd_label_pattern.search(col):
            label = col.split("::")[0].strip() if "::" in col else col
            istd_cols[label] = col
            continue
        if tpl.istd_cps_pattern and (m := tpl.istd_cps_pattern.match(col)):
            istd_cols[m.group("label").strip()] = col
            continue
        if tpl.analyte_conc_pattern and (m := tpl.analyte_conc_pattern.match(col)):
            unit = (m.groupdict().get("unit") or "ppb").strip()
            conc_cols[m.group("label").strip()] = (col, unit)
            continue
        if tpl.analyte_cps_pattern and (m := tpl.analyte_cps_pattern.match(col)):
            cps_cols[m.group("label").strip()] = col
            continue
        batch.warnings.append(f"unmapped column ignored: '{col}'")

    if not conc_cols and not cps_cols:
        raise ValueError(
            f"no analyte columns matched template '{tpl.id}' — wrong template for this export?")

    analyte_labels = list(conc_cols) or list(cps_cols)
    batch.analytes = [_analyte_from_label(lbl) for lbl in analyte_labels]
    batch.istds = [_analyte_from_label(lbl) for lbl in istd_cols]

    name_col = tpl.columns.get("sample_name", "Sample Name")
    type_col = tpl.columns.get("sample_type", "Type")
    level_col = tpl.columns.get("level")
    seq_col = tpl.columns.get("seq")
    flags_col = tpl.columns.get("flags")

    unknown_types: set[str] = set()
    for i, row_vals in enumerate(data_rows):
        row = dict(zip(names, row_vals))
        type_str = (row.get(type_col) or "").strip()
        stype = tpl.sample_type_vocab.get(type_str)
        if stype is None:
            stype = SampleType.OTHER
            if type_str not in unknown_types:
                unknown_types.add(type_str)
                batch.warnings.append(
                    f"unrecognized sample type '{type_str}' -> OTHER "
                    f"(add it to the template's sample_type_vocab)")

        sample = Sample(
            name=(row.get(name_col) or f"row{i + 1}").strip(),
            seq_index=int(_num(row.get(seq_col)) or (i + 1)) if seq_col else i + 1,
            type=stype,
        )
        if level_col and not tpl.level_is_index:
            sample.level = _num(row.get(level_col))
        if (sample.level is None and tpl.expected_conc_from_name
                and stype in _NAME_LEVEL_TYPES):
            if m := tpl.expected_conc_from_name.search(sample.name):
                sample.level = float(m.group("value"))

        for rule in tpl.parent_rules:
            if stype == rule.type and sample.name.endswith(rule.name_suffix):
                sample.parent_name = sample.name[: -len(rule.name_suffix)]
                break

        for label in analyte_labels:
            conc = unit = None
            if label in conc_cols:
                col, unit = conc_cols[label]
                conc = _num(row.get(col))
            sample.results[label] = Result(
                conc=conc,
                unit=unit or "ppb",
                intensity=_num(row.get(cps_cols[label])) if label in cps_cols else None,
            )
        for label, col in istd_cols.items():
            v = _num(row.get(col))
            if v is not None:
                sample.istd_intensities[label] = v
        if flags_col and (flag_text := (row.get(flags_col) or "").strip()):
            sample.flags.append(flag_text)

        batch.samples.append(sample)

    batch.samples.sort(key=lambda s: s.seq_index)
    return batch
