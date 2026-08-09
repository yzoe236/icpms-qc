"""Export-layout template loading (configs/*.template.yaml → Template).

A template turns one lab's export layout into the canonical model: fixed-column
names, regex patterns for analyte/ISTD columns, the sample-type vocabulary, and
parent-linking rules. Layouts are data, not code — see SPEC §2.

v0.1.1 additions (driven by real-world MassHunter exports):
  * encoding            — exports are often ANSI/cp1252, not UTF-8
  * header_rows: 2      — analyte label row + measure sub-header row
  * header_group_labels — row-1 labels that mark the sample-info block ("Sample")
  * ignore_patterns     — columns consumed silently (RSD, timestamps, ...)
  * istd_label_pattern  — route any analyte column whose label matches to ISTDs
  * expected_conc_from_name / level_is_index — real exports put a level *index*
    in the Level column; expected conc lives in the sample name ("100ppb 71A")
  * columns.flags       — instrument-software QC flag text column
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from icpms_qc.model import SampleType

_REPO_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


@dataclass
class ParentRule:
    type: SampleType
    name_suffix: str


@dataclass
class Template:
    id: str
    instrument_family: str
    columns: dict[str, str]
    analyte_conc_pattern: re.Pattern | None
    analyte_cps_pattern: re.Pattern | None
    #: Precision columns ("... :: CPS RSD"). Matched *before* ignore_patterns, so
    #: declaring it overrides an inherited 'RSD$' ignore rather than fighting it.
    analyte_rsd_pattern: re.Pattern | None
    istd_cps_pattern: re.Pattern | None
    sample_type_vocab: dict[str, SampleType]
    parent_rules: list[ParentRule]
    encoding: str = "utf-8-sig"
    header_rows: int = 1
    header_group_labels: list[str] = field(default_factory=list)
    ignore_patterns: list[re.Pattern] = field(default_factory=list)
    istd_label_pattern: re.Pattern | None = None
    expected_conc_from_name: re.Pattern | None = None
    level_is_index: bool = False
    #: "columns" (a sample per row) or "transposed" (a sample per column block,
    #: as the Thermo Element writes). A transposed layout has no analyte-column
    #: patterns to declare, because its columns are samples.
    layout: str = "columns"
    #: Name pattern -> SampleType, for layouts whose export states no type at all.
    #: Ordered: the first match wins, and anything unmatched stays OTHER + warning.
    sample_type_patterns: list[tuple[re.Pattern, SampleType]] = field(default_factory=list)
    #: What an unmatched name means, when a lab is willing to state it. Left
    #: unset the parser warns and marks OTHER, which is the right default; set in
    #: a template it is a declared policy in a reviewable file, not a guess.
    default_sample_type: SampleType | None = None


def resolve_path(name_or_path: str) -> Path:
    """Accept a direct YAML path or a bare template name looked up in configs/."""
    p = Path(name_or_path)
    if p.suffix in {".yaml", ".yml"}:
        if p.exists():
            return p
        raise FileNotFoundError(f"template file not found: {p}")
    for base in (_REPO_CONFIG_DIR, Path.cwd() / "configs"):
        cand = base / f"{name_or_path}.template.yaml"
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"template '{name_or_path}' not found in {_REPO_CONFIG_DIR} or ./configs")


def load(name_or_path: str) -> Template:
    data = yaml.safe_load(resolve_path(name_or_path).read_text(encoding="utf-8"))

    def pat(key: str) -> re.Pattern | None:
        return re.compile(data[key]) if data.get(key) else None

    layout = str(data.get("layout", "columns"))
    conc = pat("analyte_conc_pattern")
    if layout == "columns" and conc is None and not data.get("analyte_cps_pattern"):
        raise ValueError(
            "template must define analyte_conc_pattern and/or analyte_cps_pattern")

    vocab = {str(k): SampleType(str(v)) for k, v in (data.get("sample_type_vocab") or {}).items()}
    rules = [ParentRule(SampleType(str(r["type"])), str(r["name_suffix"]))
             for r in (data.get("parent_rules") or [])]

    return Template(
        id=str(data["id"]),
        instrument_family=str(data.get("instrument_family", "unknown")),
        columns={str(k): str(v) for k, v in (data.get("columns") or {}).items()},
        analyte_conc_pattern=conc,
        analyte_cps_pattern=pat("analyte_cps_pattern"),
        analyte_rsd_pattern=pat("analyte_rsd_pattern"),
        istd_cps_pattern=pat("istd_cps_pattern"),
        sample_type_vocab=vocab,
        parent_rules=rules,
        encoding=str(data.get("encoding", "utf-8-sig")),
        header_rows=int(data.get("header_rows", 1)),
        header_group_labels=[str(x) for x in (data.get("header_group_labels") or [])],
        ignore_patterns=[re.compile(str(x)) for x in (data.get("ignore_patterns") or [])],
        istd_label_pattern=pat("istd_label_pattern"),
        expected_conc_from_name=pat("expected_conc_from_name"),
        level_is_index=bool(data.get("level_is_index", False)),
        layout=layout,
        sample_type_patterns=[(re.compile(str(r["pattern"])), SampleType(str(r["type"])))
                              for r in (data.get("sample_type_patterns") or [])],
        default_sample_type=(SampleType(str(data["default_sample_type"]))
                             if data.get("default_sample_type") else None),
    )
