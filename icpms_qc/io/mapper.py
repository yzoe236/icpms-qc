"""AI-assisted template authoring: unknown export layout → reviewable template.

Why this exists
---------------
Every lab's export differs (software version, report template, custom columns), so
"bring your own YAML template" is a wall most users never climb. This module lowers
the wall: it reads an unfamiliar export, extracts a *layout fingerprint*, and asks a
language model to draft the template. A human then reviews and accepts it.

Three properties are deliberate, and none of them are negotiable:

1. **The model authors the template; it never judges QC.** Once a template is
   accepted it is a plain YAML file, and every future run of that layout is
   deterministic — the same export produces byte-identical results in three years,
   which is the whole point of a compliance tool.

2. **Only layout leaves the machine, never measurements.** The fingerprint carries
   column headers, categorical vocabularies, and per-column *kinds* — never numeric
   values, and never free-text that fails the redaction allowlist. Client sample
   names are masked by default (``--include-names`` opts out, and says so).

3. **A draft is a proposal, not a result.** Every draft is validated by actually
   parsing the export with it; the report tells you what matched and what did not.
   Nothing is written to ``configs/`` without an explicit human accept.

The LLM step is optional. ``fingerprint()`` and ``validate()`` are pure Python, so
``icpms-qc inspect`` works offline and the fingerprint can be handed to any model — or
to a person — to author the template by hand.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from icpms_qc.io import masshunter, templates

MAX_SCAN_ROWS = 400
MAX_DISTINCT = 12
CATEGORICAL_MAX = 15
MAX_VALUE_CHARS = 160        # instrument QC blobs run to thousands of characters


SHORT_VALUE_CHARS = 24       # beyond this a cell is prose, not a code


def _clip(value: str) -> str:
    return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + " …"

# ── redaction ────────────────────────────────────────────────────────────────
# Data cells are masked unless every token is recognizably lab vocabulary. The
# allowlist is intentionally small: QC nomenclature, units, and numbers pass; a
# client's site code does not.
_QC_WORDS = {
    "blank", "blk", "cal", "calstd", "calblk", "calib", "calibration", "std",
    "standard", "ccv", "icv", "ccb", "icb", "cvb", "mb", "methodblank", "lcs",
    "lcsd", "dup", "duplicate", "ms", "msd", "spike", "matrix", "qc", "qcs",
    "rinse", "wash", "tune", "sample", "smp", "unk", "unknown", "level", "lvl",
    "check", "recovery", "rec", "serial", "dilution", "dil", "initial",
    "continuing", "verification", "start", "end", "begin", "final", "post",
    "pre", "low", "mid", "high", "top", "zero", "none", "na", "n/a", "true",
    "false", "yes", "no", "pass", "fail", "ok",
    # header-ish tokens (so a real header row survives redaction intact)
    "conc", "concentration", "cps", "counts", "intensity", "rsd", "sd", "mean",
    "avg", "average", "unit", "units", "isotope", "mass", "element", "analyte",
    "type", "name", "seq", "sequence", "id", "index", "no", "num", "time",
    "date", "istd", "is", "internal", "flag", "flags", "comment", "note",
    "acq", "acquired", "vial", "position", "pos", "rep", "replicate", "factor",
    "df", "result", "value", "data", "batch", "run", "method", "file",
}
_UNITS = {"ppb", "ppm", "ppt", "ppq", "mg/l", "µg/l", "ug/l", "ng/l", "mg/kg",
          "ug/kg", "ng/g", "ug/g", "mg/g", "%", "cps", "µg/g", "counts/s"}
_ELEMENTS = {
    "h", "he", "li", "be", "b", "c", "n", "o", "f", "ne", "na", "mg", "al", "si",
    "p", "s", "cl", "ar", "k", "ca", "sc", "ti", "v", "cr", "mn", "fe", "co",
    "ni", "cu", "zn", "ga", "ge", "as", "se", "br", "kr", "rb", "sr", "y", "zr",
    "nb", "mo", "tc", "ru", "rh", "pd", "ag", "cd", "in", "sn", "sb", "te", "i",
    "xe", "cs", "ba", "la", "ce", "pr", "nd", "pm", "sm", "eu", "gd", "tb",
    "dy", "ho", "er", "tm", "yb", "lu", "hf", "ta", "w", "re", "os", "ir", "pt",
    "au", "hg", "tl", "pb", "bi", "th", "u", "he", "h2", "o2", "nh3", "n2o",
}
_NUMERIC = re.compile(r"^[+-]?[\d.,]+(?:[eE][+-]?\d+)?$")
_SPLIT = re.compile(r"([\s\-_,;:()\[\]/\\|]+)")
_MASK = "█"


def _compound_safe(t: str) -> bool:
    """Recognize vocabulary run together: calblank, methodblank, ccvblank."""
    for i in range(2, len(t) - 1):
        if t[:i] in _QC_WORDS and t[i:] in _QC_WORDS:
            return True
    return False


def _token_safe(tok: str, has_mass_context: bool = False,
                allow_bare_numbers: bool = True) -> bool:
    # Trailing punctuation is ornamental: a sub-header cell reads "Conc." but the
    # vocabulary entry is "conc". Without this, real header rows get masked and the
    # model goes blind to the layout it is supposed to read.
    t = tok.lower().strip().strip(".'\"*#")
    if not t:
        return True
    if _NUMERIC.match(t):
        return allow_bare_numbers
    # Element symbols are ambiguous as English ("In", "As", "No", "B"), so they are
    # vocabulary only next to a mass number — "9 Be", never "In-Situ Inc".
    if has_mass_context and t in _ELEMENTS:
        return True
    return (t in _QC_WORDS or t in _UNITS or _compound_safe(t)
            or bool(re.fullmatch(r"(?:std|lvl|level|l|cal|qc|ccv)\d+", t))
            or bool(re.fullmatch(r"\d+(?:ppb|ppm|ppt|mg/l|ug/l|%)", t)))


def _redact_cell(value: str) -> str:
    """Redact a data cell, deciding about bare numbers by the shape of the value.

    Short cells are codes and levels — "3", "1000ppb 71A" — and the model needs
    them. Long cells are prose: sample descriptions, or the instrument's own QC
    warnings, whose embedded numbers *are* measurements ("CPS RSD value 14.81").
    Length, not the column's cardinality, is what separates the two: a one-batch
    export can carry a single distinct warning blob and still look categorical.
    """
    return redact(value, allow_bare_numbers=len(value) <= SHORT_VALUE_CHARS)


def redact(value: str, allow_bare_numbers: bool = True) -> str:
    """Mask any token that is not recognizable lab vocabulary.

    A real header cell ("9 Be Conc. [ppb]") survives intact; a client sample name
    ("Northside Well 3") does not.

    ``allow_bare_numbers=False`` additionally masks standalone numbers. Use it for
    rows that might be data: a genuine sub-header row carries units and measure
    names but never bare values, so masking numbers costs nothing there and stops
    measurements from riding along when the header turned out to be one row.
    """
    if value is None:
        return ""
    has_mass = bool(re.search(r"\d", value))
    out = []
    for part in _SPLIT.split(value):
        if _SPLIT.fullmatch(part) or _token_safe(part, has_mass, allow_bare_numbers):
            out.append(part)
        else:
            out.append(_MASK * min(len(part), 6))
    return "".join(out)


# ── fingerprint ──────────────────────────────────────────────────────────────
@dataclass
class ColumnProfile:
    index: int
    header: str
    kind: str                        # numeric | categorical | text | empty
    distinct: int = 0
    values: list[str] = field(default_factory=list)


@dataclass
class Fingerprint:
    path: str
    encoding: str
    n_columns: int
    n_rows_scanned: int
    head_rows: list[list[str]]       # first rows, data cells redacted
    columns: list[ColumnProfile]
    names_included: bool

    def to_dict(self) -> dict:
        return {
            "source_file": Path(self.path).name,
            "encoding_detected": self.encoding,
            "n_columns": self.n_columns,
            "n_rows_scanned": self.n_rows_scanned,
            "sample_names_included": self.names_included,
            "first_rows": self.head_rows,
            "columns": [
                {k: v for k, v in
                 {"i": c.index, "header": c.header, "kind": c.kind,
                  "distinct": c.distinct or None,
                  "values": c.values or None}.items() if v is not None}
                for c in self.columns
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _read_rows(path: str) -> tuple[list[list[str]], str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                rows = []
                for i, row in enumerate(csv.reader(fh)):
                    rows.append(row)
                    if i >= MAX_SCAN_ROWS:
                        break
                return rows, enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: could not decode with utf-8/cp1252/latin-1")


def _looks_like_subheader(rows: list[list[str]], width: int) -> bool:
    """Is row 1 a second header row rather than the first data row?

    This matters more than it looks: if a sub-header row is profiled as data, every
    measurement column contains one text cell, stops looking numeric, and gets its
    values listed in the fingerprint — which is exactly how concentrations would
    leak. Real two-row exports are common (analyte labels above Conc./CPS/RSD), so
    detect them rather than trust a flag.
    """
    if len(rows) < 3:
        return False

    def numeric_share(row: list[str]) -> float:
        cells = [(row[i].strip() if i < len(row) else "") for i in range(width)]
        filled = [c for c in cells if c]
        if not filled:
            return 0.0
        return sum(bool(_NUMERIC.match(c)) for c in filled) / len(filled)

    # Take the *best* data row in a wide window, not an average of the first few:
    # real sequences open with rinses and blanks whose cells are empty or textual,
    # and averaging over them hides the numeric rows that prove row 1 is a header.
    below = [numeric_share(r) for r in rows[2:40]]
    if not below:
        return False
    return numeric_share(rows[1]) < 0.2 and max(below) > 0.5


def fingerprint(path: str, include_names: bool = False,
                head: int = 3) -> Fingerprint:
    """Extract layout-only structure from an unfamiliar export.

    Header rows are sent verbatim (they are structure, not data). Everything below
    is redacted unless ``include_names`` is set — and even then only categorical
    columns list their values.
    """
    rows, enc = _read_rows(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    width = max(len(r) for r in rows)

    # Row 0 is a header by definition; rows 1-2 may be a sub-header or data, so they
    # go through redaction with numbers masked — a genuine sub-header passes intact,
    # while a data row that slipped in surrenders its measurements.
    head_rows = [[c.strip() for c in rows[0]]]
    for r in rows[1:head]:
        head_rows.append([c.strip() if include_names
                          else redact(c.strip(), allow_bare_numbers=False)
                          for c in r])

    # Profile only true data rows, so a sub-header cannot disguise a measurement
    # column as free text.
    body = rows[2:] if _looks_like_subheader(rows, width) else rows[1:]
    profiles = []
    for i in range(width):
        col = [(r[i].strip() if i < len(r) else "") for r in body]
        nonempty = [v for v in col if v]
        header = rows[0][i].strip() if i < len(rows[0]) else ""
        if not nonempty:
            profiles.append(ColumnProfile(i, header, "empty"))
            continue

        numeric_share = sum(bool(_NUMERIC.match(v)) for v in nonempty) / len(nonempty)
        if numeric_share > 0.5:
            # Predominantly numeric: measurements. Report the shape, never the values.
            profiles.append(ColumnProfile(i, header, "numeric",
                                          distinct=len(set(nonempty))))
            continue

        distinct = sorted(set(nonempty))
        categorical = len(distinct) <= CATEGORICAL_MAX
        kind = "categorical" if categorical else "text"
        sample = [_clip(v) for v in distinct[:MAX_DISTINCT if categorical else 4]]
        profiles.append(ColumnProfile(
            i, header, kind, len(distinct),
            sample if include_names else [_redact_cell(v) for v in sample]))

    return Fingerprint(path, enc, width, len(body), head_rows, profiles,
                       include_names)


# ── prompt ───────────────────────────────────────────────────────────────────
_SCHEMA_DOC = """\
Template schema (all keys optional unless marked required):

  id:                    required, snake_case identifier
  instrument_family:     required, e.g. agilent-masshunter | thermo-qtegra |
                         perkinelmer-syngistix | unknown
  encoding:              utf-8-sig (default) | cp1252
  header_rows:           1 (default) or 2 — use 2 when analyte labels span a row
                         above a sub-header row of measures (Conc./CPS/RSD)
  header_group_labels:   row-1 labels marking the sample-info block, e.g. ["Sample"]
  columns:               map of canonical -> exact column name, keys:
                           seq, sample_name, sample_type, level, flags
                         For header_rows: 2, analyte-region columns are addressed
                         as "<row1 label> :: <row2 label>"; sample-info columns use
                         the row-2 name alone.
  analyte_conc_pattern:  regex over column names with named groups (?P<label>...)
                         and optional (?P<unit>...) — matches concentration columns
  analyte_cps_pattern:   regex with (?P<label>...) — matches raw-intensity columns
  istd_cps_pattern:      regex with (?P<label>...) — internal-standard columns
  istd_label_pattern:    regex; ANY column whose name matches routes to ISTDs
                         (use when ISTDs are marked in the label, e.g. "(ISTD)")
  ignore_patterns:       list of regexes for columns to drop silently (RSD, times)
  sample_type_vocab:     map of export string -> canonical type, chosen from:
                         CAL_BLANK CAL_STD ICV ICB CCV CCB MB LCS LCSD SAMPLE
                         DUP MS MSD SERIAL_DIL OTHER
  parent_rules:          list of {type: DUP|MS|MSD, name_suffix: "-DUP"} linking
                         a QC aliquot back to its parent sample by name suffix
  level_is_index:        true when the level column holds an ordinal (1,2,3),
                         not a concentration
  expected_conc_from_name: regex with (?P<value>...) pulling expected concentration
                         out of a standard's name, e.g. "100ppb 71A"
"""

_PROMPT = """\
You are drafting an icpms-qc export-layout template for an ICP-MS batch export whose \
layout is unknown. You are given a layout fingerprint: header rows verbatim, and \
per-column kind plus categorical vocabularies. Measurement values are withheld by \
design, and non-vocabulary text is masked with {mask} — treat masked text as \
"opaque free text", never as meaningful content.

{schema}

Rules:
- Map ONLY what the fingerprint supports. Guessing costs more than omitting: an \
absent mapping produces a loud NOT_EVALUATED, while a wrong one produces a \
confident falsehood.
- Every value in the sample-type column that plausibly denotes a QC role belongs \
in sample_type_vocab. Leave genuinely unclear strings out; they will surface as \
warnings for the human reviewer.
- Regexes are Python `re`, matched against the whole column name with .match(). \
Escape literal dots and brackets.
- If a header row is repeated as a sub-header of measures, set header_rows: 2 and \
address analyte columns as "<label> :: <sub>".

Return ONLY a YAML document — no prose, no code fences. After the YAML, append \
comment lines beginning with `# NOTE:` for anything you were unsure about or chose \
not to map.

FINGERPRINT:
{fingerprint}
"""


def build_prompt(fp: Fingerprint) -> str:
    return _PROMPT.format(mask=_MASK, schema=_SCHEMA_DOC, fingerprint=fp.to_json())


# ── model call ───────────────────────────────────────────────────────────────
def call_model(prompt: str, timeout: int = 600) -> str:
    """Ask a model for the draft. Prefers the local `claude` CLI, then the API.

    Raises RuntimeError with an actionable message when neither is available —
    the fingerprint is still usable by hand, so this is never a dead end.
    """
    cli = shutil.which("claude")
    if cli:
        # Prompt goes on stdin, not argv: a fingerprint of a wide export blows past
        # the Windows 8k command-line limit.
        proc = subprocess.run([cli, "-p"], input=prompt, capture_output=True,
                              text=True, timeout=timeout, encoding="utf-8",
                              errors="replace")
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
        raise RuntimeError(
            f"claude CLI failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}")

    if os.environ.get("ANTHROPIC_API_KEY"):
        import urllib.request
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({
                "model": os.environ.get("ICPQC_MODEL", "claude-sonnet-5"),
                "max_tokens": 4000,
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={"content-type": "application/json",
                     "anthropic-version": "2023-06-01",
                     "x-api-key": os.environ["ANTHROPIC_API_KEY"]})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return "".join(b.get("text", "") for b in body.get("content", []))

    raise RuntimeError(
        "no model available: install the `claude` CLI or set ANTHROPIC_API_KEY.\n"
        "You can still author the template by hand — run `icpms-qc inspect` and use "
        "the fingerprint it prints.")


def extract_yaml(text: str) -> str:
    """Pull the YAML document out of a model reply (tolerates fences/preamble)."""
    if "```" in text:
        blocks = re.findall(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.S)
        if blocks:
            return max(blocks, key=len).strip() + "\n"
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*id\s*:", line):
            return "\n".join(lines[i:]).strip() + "\n"
    return text.strip() + "\n"


# ── validation ───────────────────────────────────────────────────────────────
@dataclass
class Validation:
    ok: bool
    error: str | None
    analytes: list[str] = field(default_factory=list)
    istds: list[str] = field(default_factory=list)
    type_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    n_samples: int = 0

    @property
    def unmapped_columns(self) -> list[str]:
        return [w for w in self.warnings if w.startswith("unmapped column")]

    @property
    def unknown_types(self) -> list[str]:
        return [w for w in self.warnings if w.startswith("unrecognized sample type")]


def validate(template_yaml: str, export_csv: str) -> Validation:
    """Prove the draft on the real file: parse it and report what it understood."""
    tmp = Path(tempfile.mkdtemp(prefix="icpqc_tpl_")) / "candidate.template.yaml"
    tmp.write_text(template_yaml, encoding="utf-8")
    try:
        templates.load(str(tmp))                       # schema/regex validity
        batch = masshunter.parse(export_csv, template=str(tmp))
    except Exception as exc:                            # noqa: BLE001 — reported
        return Validation(False, f"{type(exc).__name__}: {exc}")

    counts: dict[str, int] = {}
    for s in batch.samples:
        counts[s.type.value] = counts.get(s.type.value, 0) + 1
    return Validation(
        ok=True, error=None,
        analytes=[a.label for a in batch.analytes],
        istds=[a.label for a in batch.istds],
        type_counts=counts,
        warnings=batch.warnings,
        n_samples=len(batch.samples),
    )


def review_report(v: Validation, fp: Fingerprint) -> str:
    """Human-facing verdict. Silence about a gap would be the one unforgivable bug."""
    if not v.ok:
        return f"  DRAFT REJECTED — it does not parse this export:\n    {v.error}"

    lines = [f"  samples parsed : {v.n_samples}",
             f"  analytes       : {len(v.analytes)}"
             + (f"  ({', '.join(v.analytes[:6])}"
                + (", …" if len(v.analytes) > 6 else "") + ")" if v.analytes else ""),
             f"  internal stds  : {len(v.istds)}"
             + (f"  ({', '.join(v.istds)})" if v.istds else "")]

    if v.type_counts:
        shown = ", ".join(f"{k}×{n}" for k, n in sorted(v.type_counts.items()))
        lines.append(f"  sample types   : {shown}")

    other = v.type_counts.get("OTHER", 0)
    if other:
        lines.append(f"  !! {other} row(s) fell through to OTHER — QC checks that "
                     f"need them will report NOT_EVALUATED")
    for w in v.unknown_types:
        lines.append(f"     - {w}")

    unmapped = v.unmapped_columns
    if unmapped:
        lines.append(f"  !! {len(unmapped)} column(s) unmapped:")
        lines.extend(f"     - {w.split(':', 1)[1].strip()}" for w in unmapped[:10])
        if len(unmapped) > 10:
            lines.append(f"     … and {len(unmapped) - 10} more")

    residual = [w for w in v.warnings
                if w not in v.unmapped_columns and w not in v.unknown_types]
    lines.extend(f"  note: {w}" for w in residual)

    coverage = len(v.analytes) + len(v.istds)
    if coverage and not unmapped and not other:
        lines.append("  → clean parse: every column and every sample type accounted for")
    else:
        lines.append("  → review the flagged items above before accepting")
    return "\n".join(lines)


def draft(export_csv: str, template_id: str | None = None,
          include_names: bool = False) -> tuple[str, Validation, Fingerprint]:
    """Fingerprint → model → validated draft. Writes nothing; the caller decides."""
    fp = fingerprint(export_csv, include_names=include_names)
    text = call_model(build_prompt(fp))
    tpl_yaml = extract_yaml(text)
    if template_id:
        tpl_yaml = re.sub(r"^\s*id\s*:.*$", f"id: {template_id}", tpl_yaml,
                          count=1, flags=re.M)
    return tpl_yaml, validate(tpl_yaml, export_csv), fp


# ── second pass: resolve ambiguous sample types ──────────────────────────────
# Masking names is what keeps client data home, and it is also what stops the first
# pass from telling ICV from CCV — the two are distinguished only by what the rows
# are called. The resolution is not to unmask everything, but to unmask the few rows
# that carry an unresolved type: those are standards and QC aliquots, named by the
# lab ("CCV 50ppb"), not by its clients.
MAX_RESOLVE_ROWS = 24        # a batch's QC rows are few; anything more is samples
MAX_RESOLVE_SHARE = 0.30     # a type covering a third of the batch is the samples


@dataclass
class Disclosure:
    """What a resolution pass would reveal, so it can be shown before it is sent."""
    names_by_type: dict[str, list[str]]
    skipped: dict[str, str]                       # type value -> why it was skipped

    @property
    def empty(self) -> bool:
        return not self.names_by_type

    def describe(self) -> str:
        lines = []
        for tval, names in self.names_by_type.items():
            lines.append(f"    {tval!r}: {', '.join(repr(n) for n in names)}")
        for tval, why in self.skipped.items():
            lines.append(f"    {tval!r}: withheld — {why}")
        return "\n".join(lines)


def _column_index(names: list[str], target: str) -> int | None:
    return names.index(target) if target in names else None


def plan_resolution(template_yaml: str, export_csv: str) -> Disclosure:
    """Decide which sample names a resolution pass would disclose, and which not."""
    data = yaml.safe_load(template_yaml) or {}
    cols = data.get("columns") or {}
    type_col, name_col = cols.get("sample_type"), cols.get("sample_name")
    mapped = set((data.get("sample_type_vocab") or {}).keys())
    if not type_col or not name_col:
        return Disclosure({}, {})

    # Resolve column positions the way the parser will, so a two-row header's
    # "label :: sub" naming lines up with what the template declares.
    tmp = Path(tempfile.mkdtemp(prefix="icpqc_res_")) / "t.template.yaml"
    tmp.write_text(template_yaml, encoding="utf-8")
    try:
        tpl = templates.load(str(tmp))
    except (ValueError, KeyError):
        return Disclosure({}, {})
    rows, _ = _read_rows(export_csv)
    if len(rows) <= tpl.header_rows:
        return Disclosure({}, {})
    names, body = masshunter._logical_header(rows, tpl)

    ti, ni = _column_index(names, type_col), _column_index(names, name_col)
    if ti is None or ni is None:
        return Disclosure({}, {})

    buckets: dict[str, list[str]] = {}
    for row in body:
        tval = (row[ti].strip() if ti < len(row) else "")
        if not tval or tval in mapped:
            continue
        nval = (row[ni].strip() if ni < len(row) else "")
        if nval:
            buckets.setdefault(tval, []).append(nval)

    total = max(len(body), 1)
    disclose, skipped = {}, {}
    for tval, vals in buckets.items():
        if len(vals) > MAX_RESOLVE_ROWS or len(vals) / total > MAX_RESOLVE_SHARE:
            skipped[tval] = (f"{len(vals)} rows ({len(vals) / total:.0%} of the batch)"
                             f" — too many to be QC; these look like client samples")
            continue
        seen, uniq = set(), []
        for v in vals:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        disclose[tval] = uniq
    return Disclosure(disclose, skipped)


_RESOLVE_PROMPT = """\
You previously drafted this icpms-qc template. Some sample-type strings were left \
unmapped because their canonical role could not be told apart from layout alone. \
Here are the sample names of the rows carrying each unresolved type — these are \
calibration standards and QC aliquots named by the lab, disclosed for this purpose \
only.

UNRESOLVED TYPES AND THE NAMES OF THEIR ROWS:
{disclosure}

CURRENT TEMPLATE:
{template}

Decide, for each unresolved type string, whether the names now determine its \
canonical role. Choose from: CAL_BLANK CAL_STD ICV ICB CCV CCB MB LCS LCSD SAMPLE \
DUP MS MSD SERIAL_DIL OTHER.

If one type string clearly covers two different roles (for example both an initial \
and a continuing verification), say so in a NOTE and leave it unmapped — a template \
cannot split one string two ways, and a wrong choice corrupts the checks that \
depend on it.

Also propose parent_rules if the names reveal a suffix convention linking a QC \
aliquot to its parent sample (e.g. "-DUP", " MS").

Return ONLY a YAML fragment with at most these two keys — no prose, no fences:

  sample_type_vocab:      # ONLY the newly resolved entries
    <string>: <TYPE>
  parent_rules:           # omit entirely if no convention is visible
    - {{type: DUP, name_suffix: "-DUP"}}

After the YAML, append `# NOTE:` lines for anything you left unresolved and why.
"""


def merge_fragment(template_yaml: str, fragment_yaml: str) -> str:
    """Fold resolved vocabulary into the template, preserving everything else."""
    frag = yaml.safe_load(fragment_yaml) or {}
    if not isinstance(frag, dict):
        return template_yaml
    data = yaml.safe_load(template_yaml) or {}
    vocab = dict(data.get("sample_type_vocab") or {})
    vocab.update(frag.get("sample_type_vocab") or {})
    if vocab:
        data["sample_type_vocab"] = vocab
    if frag.get("parent_rules"):
        data["parent_rules"] = frag["parent_rules"]
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          default_flow_style=False)


def resolve(template_yaml: str, export_csv: str,
            disclosure: Disclosure) -> tuple[str, Validation]:
    """Second pass over the disclosed names; returns the merged, re-validated template."""
    text = call_model(_RESOLVE_PROMPT.format(
        disclosure=disclosure.describe(), template=template_yaml))
    merged = merge_fragment(template_yaml, extract_yaml(text))
    return merged, validate(merged, export_csv)
