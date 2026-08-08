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
from icpqc.model import Analyte, Batch, InstrumentFlag, Result, Sample, SampleType

# Analyte labels, every shape a MassHunter export writes them in:
#   "9 Be"                 single quad, no cell gas
#   "75 As [He]"           single quad, collision mode
#   "56  Fe  [ No Gas ]"   padded — MassHunter aligns these columns
#   "78 -> 78 Se [He]"     triple quad (8800/8900) MS/MS, on-mass
#   "31 -> 47 P [O2]"      triple quad, mass-shift onto a reaction product
# Without the mass-shift branch a QQQ export parses to mass=None/element=None
# and every check that groups by element goes quietly blind.
_LABEL_RE = re.compile(
    r"""^\s*(?P<mass>\d+)\s*                    # Q1 mass
        (?:->\s*(?P<mass_shift>\d+)\s*)?        # Q2 mass, MS/MS only
        \[?\s*(?P<element>[A-Z][a-z]?)(?![a-z])\s*\]?   # element symbol, whole word
        (?:\s*\[\s*(?P<mode>[^\]]*?)\s*\])?     # cell/reaction mode
    """,
    re.VERBOSE,
)

_WS = re.compile(r"\s+")

#: exact strings an export uses for "measured, not detected" (no limit quoted)
_NON_DETECT = {"nd", "n.d.", "n.d", "<dl", "<mdl", "<loq", "<lod"}

#: Real element symbols, so a header that merely *looks* like "<mass> <Xx>"
#: cannot invent an element (see _analyte_from_label).
ELEMENT_SYMBOLS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La
Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po
At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg
Cn Nh Fl Mc Lv Ts Og
""".split())

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


# MassHunter objects in both directions, and a calibration R may be negative:
#   "CPS RSD value = 12.10 is over the allowed maximum = 5.00"
#   "Calibration Curve Fit R value = -0.286775 is below the allowed minimum = 0.950000"
_FLAG_BODY = re.compile(
    r"^\s*:?\s*(?P<metric>.*?)\s*value\s*=\s*(?P<value>[-+]?[\d.]+)\s+is\s+"
    r"(?P<direction>over the allowed maximum|below the allowed minimum)"
    r"\s*=\s*(?P<limit>[-+]?[\d.]+)\s*$")

# A third wording states no numeric limit, because the bound is the calibration
# itself: "Concentration value = 2202.42 is over the calibration range". That is
# a reportability finding in its own right — the result is extrapolated past the
# highest standard — so it must not be lumped in with unparsed text.
_FLAG_RANGE = re.compile(
    r"^\s*:?\s*(?P<metric>.*?)\s*value\s*=\s*(?P<value>[-+]?[\d.]+)\s+is\s+"
    r"(?P<direction>over|below)\s+the\s+calibration\s+range\s*$")


def parse_instrument_flags(text: str, analyte_labels: list[str]) -> list[InstrumentFlag]:
    """Split MassHunter's QC warning blob into one flag per analyte.

    The blob concatenates sentences with no delimiter, so a limit runs straight
    into the next analyte's mass::

        ... allowed maximum = 5.0066  Zn  [ No Gas ] :  CPS RSD value = 12.10 ...
                              ^^^^ ^^
                              5.00 and mass 66, with nothing between them

    That split is genuinely ambiguous to a regex — 5.0066 could be 5.00 + 66,
    5.006 + 6, or 5.0 + 066. It is not ambiguous to us: the batch header already
    told us which analyte labels exist, so the blob is cut at those known
    strings instead of guessed at.
    """
    if not text:
        return []
    marks: list[tuple[int, str]] = []
    for label in analyte_labels:
        start = 0
        while (i := text.find(label, start)) != -1:
            marks.append((i, label))
            start = i + 1
    marks.sort(key=lambda m: (m[0], -len(m[1])))

    picked: list[tuple[int, str]] = []
    for pos, label in marks:
        if picked and pos < picked[-1][0] + len(picked[-1][1]):
            continue                                  # overlaps the previous label
        picked.append((pos, label))

    out: list[InstrumentFlag] = []
    for k, (pos, label) in enumerate(picked):
        end = picked[k + 1][0] if k + 1 < len(picked) else len(text)
        body = text[pos + len(label):end]
        m = _FLAG_BODY.match(body)
        if m:
            out.append(InstrumentFlag(
                analyte=label, metric=m.group("metric").strip(),
                value=_num(m.group("value")), limit=_num(m.group("limit")),
                direction="high" if "maximum" in m.group("direction") else "low",
                text=(label + body).strip()))
        elif (m := _FLAG_RANGE.match(body)):
            out.append(InstrumentFlag(
                analyte=label,
                metric=f"{m.group('metric').strip()} outside calibration range",
                value=_num(m.group("value")), limit=None,
                direction="high" if m.group("direction") == "over" else "low",
                text=(label + body).strip()))
        else:
            # Unrecognized wording still counts as an objection; carry it whole
            # rather than dropping the vendor's verdict on a phrasing change.
            out.append(InstrumentFlag(analyte=label, metric=body.strip(" :") or "flagged",
                                      text=(label + body).strip()))
    return out


def _parse_conc(raw: str | None) -> tuple[float | None, bool, float | None]:
    """Split a reported concentration into (value, below_dl, detection_limit).

    MassHunter writes a non-detect as "<0.05", not as a number. Feeding that to
    float() raises, so the naive parser drops it — and a blank that was cleanly
    below detection becomes indistinguishable from a blank nobody measured. The
    first reports PASS, the second must not.
    """
    if raw is None:
        return None, False, None
    s = raw.strip()
    if not s or s in {"-", "N/A", "n/a", "NA"}:
        return None, False, None
    if s.lower() in _NON_DETECT:
        return None, True, None                 # censored, no limit quoted
    if s.startswith("<"):
        return None, True, _num(s[1:])          # censored at a stated limit
    return _num(s), False, None


def _analyte_from_label(label: str) -> Analyte:
    m = _LABEL_RE.match(label)
    if not m:
        return Analyte(label=label)
    sym = m.group("element")
    shift, mode = m.group("mass_shift"), m.group("mode")
    return Analyte(
        label=label,
        mass=int(m.group("mass")),
        # Belt and braces against a header that merely looks elemental: the
        # regex's (?![a-z]) rejects a truncated word ("220 Bkg" -> not Bk), and
        # the symbol set rejects a well-formed non-element ("220 Xx").
        element=sym if sym in ELEMENT_SYMBOLS else None,
        mass_shift=int(shift) if shift else None,
        mode=_WS.sub(" ", mode).strip() if mode else None,
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

    rsd_cols: dict[str, str] = {}                # analyte label -> column

    for col in names:
        if col in fixed_cols:
            continue
        # Precision is matched before the ignore list: a template that declares
        # analyte_rsd_pattern means it, and should not have to also unset an
        # inherited 'RSD$' ignore rule.
        if tpl.analyte_rsd_pattern and (m := tpl.analyte_rsd_pattern.match(col)):
            rsd_cols[m.group("label").strip()] = col
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
    batch.flags_column_mapped = bool(flags_col)

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
            conc = unit = dl = None
            below_dl = False
            if label in conc_cols:
                col, unit = conc_cols[label]
                conc, below_dl, dl = _parse_conc(row.get(col))
            sample.results[label] = Result(
                conc=conc,
                unit=unit or "ppb",
                intensity=_num(row.get(cps_cols[label])) if label in cps_cols else None,
                below_dl=below_dl,
                dl=dl,
                rsd_pct=_num(row.get(rsd_cols[label])) if label in rsd_cols else None,
            )
        for label, col in istd_cols.items():
            v = _num(row.get(col))
            if v is not None:
                sample.istd_intensities[label] = v
        if flags_col and (flag_text := (row.get(flags_col) or "").strip()):
            sample.flags.append(flag_text)
            sample.instrument_flags = parse_instrument_flags(flag_text, analyte_labels)

        batch.samples.append(sample)

    batch.samples.sort(key=lambda s: s.seq_index)
    return batch
