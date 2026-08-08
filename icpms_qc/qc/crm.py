"""Certified reference material library (configs/crm/*.yaml → CRM).

Why this is its own layer
-------------------------
Every other recovery check in icpms-qc divides by ``Sample.level`` — one expected
concentration that applies to every analyte, which is exactly right for a spike
made from one multi-element standard. A certified reference material is not that
shape: NIST SRM 1640a certifies ~30 elements at ~30 *different* values, each with
its own uncertainty, and the batch export carries none of them. The expected
values have to come from the certificate, so they live here: one YAML file per
material, versioned in git next to the rule packs, reviewable like any other
policy.

Matching is by sample name, because that is the only handle an export gives you —
a CRM is typed ``LCS`` or ``QC`` or ``Sample`` depending on the lab. Each file
declares the name patterns that identify it, so a lab that calls it "SRM1640a",
"1640-A" or "CRM-W-2" says so once.

Units are converted, never assumed: a certificate in mg/L against results in ppb
is the single easiest way to be wrong by 1000x, so an unrecognized unit produces
a NOT-ASSESSED row rather than a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_REPO_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"

#: Mass-concentration units → factor to ppb. ppb is µg/L for liquids and µg/kg
#: for solids; the two never appear in one certificate, so one table serves both.
_UNIT_FACTORS = {
    "ppb": 1.0, "ug/l": 1.0, "ng/ml": 1.0, "ug/kg": 1.0, "ng/g": 1.0,
    "ppm": 1e3, "mg/l": 1e3, "ug/ml": 1e3, "mg/kg": 1e3, "ug/g": 1e3,
    "ppt": 1e-3, "ng/l": 1e-3, "pg/ml": 1e-3, "pg/g": 1e-3,
    "%": 1e7,
}


def normalize_unit(unit: str | None) -> str:
    """Fold the spellings one unit arrives in: 'µg/L', 'μg/l', ' [ug/L] ' → 'ug/l'."""
    if not unit:
        return ""
    u = unit.strip().strip("[]() ").lower()
    # U+00B5 MICRO SIGN and U+03BC GREEK SMALL LETTER MU both occur in exports.
    return u.replace("µ", "u").replace("μ", "u").replace(" ", "")


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert between mass-concentration units, or None if either is unknown."""
    a, b = normalize_unit(from_unit), normalize_unit(to_unit)
    if a == b:
        return value
    fa, fb = _UNIT_FACTORS.get(a), _UNIT_FACTORS.get(b)
    if fa is None or fb is None:
        return None
    return value * fa / fb


#: How much authority a reference value carries. Certificates state one kind;
#: geochemical compilations (GeoReM and friends) state several, and the
#: difference is not cosmetic — an information value can be a placeholder from a
#: single lab, and failing a batch on one would be indefensible.
#:   certified   — certificate of analysis, metrologically traceable
#:   reference   — compilation "preferred"/reference value (GeoReM, USGS)
#:   information — indicative only; reported, never decisive
VALUE_TYPES = {"certified", "reference", "information"}

#: Types that may decide a pass/fail. `information` is deliberately absent.
DECISIVE_TYPES = {"certified", "reference"}


@dataclass
class CertValue:
    value: float
    #: As stated by the source — an expanded uncertainty (k=2) on a certificate,
    #: usually a 1s or 2s spread in a compilation. Describes the material, not
    #: the lab's acceptance window.
    uncertainty: float | None = None
    value_type: str = "certified"

    @property
    def decisive(self) -> bool:
        return self.value_type in DECISIVE_TYPES


@dataclass
class Provenance:
    """Where the numbers came from. For a compilation this is load-bearing.

    A certificate identifies itself; a compilation does not — GeoReM's preferred
    value for BCR-2G changes between data set versions, so a report that cannot
    say which version it used cannot be reproduced.
    """
    compilation: str = ""       # e.g. "GeoReM", "USGS", "certificate"
    version: str = ""           # data set / certificate revision
    accessed: str = ""          # YYYY-MM-DD
    citation: str = ""

    def describe(self) -> str:
        bits = [b for b in (self.compilation, self.version,
                            f"accessed {self.accessed}" if self.accessed else "") if b]
        return " · ".join(bits)


@dataclass
class CRM:
    id: str
    name: str
    unit: str
    certified: dict[str, CertValue]           # element symbol → reference value
    patterns: list[re.Pattern] = field(default_factory=list)
    matrix: str = ""
    source: str = ""
    note: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    #: elements present in the file but left unfilled — surfaced, never silent
    unfilled: list[str] = field(default_factory=list)

    def matches(self, sample_name: str) -> bool:
        return any(p.search(sample_name) for p in self.patterns)


def _cert_value(element: str, spec, default_type: str, origin: str) -> CertValue | None:
    """One reference value, or None when the entry is a placeholder to be filled.

    A half-filled library is the normal state of a real one — a lab transcribes
    the elements it measures and leaves the rest. `Sr: {value: }` is that state,
    and it must neither crash the run nor quietly behave like a real value.
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):                 # bare number: value only
        return CertValue(float(spec), value_type=default_type)
    if spec.get("value") is None:
        return None
    vtype = str(spec.get("type", default_type)).lower()
    if vtype not in VALUE_TYPES:
        raise ValueError(f"{origin}: {element} has unknown value type {vtype!r} "
                         f"(use one of {', '.join(sorted(VALUE_TYPES))})")
    unc = spec.get("uncertainty")
    return CertValue(float(spec["value"]),
                     None if unc is None else float(unc), vtype)


def _crm_from_dict(data: dict, origin: str) -> CRM:
    default_type = str(data.get("default_value_type", "certified")).lower()
    if default_type not in VALUE_TYPES:
        raise ValueError(f"{origin}: unknown default_value_type {default_type!r}")

    certified, unfilled = {}, []
    for element, spec in (data.get("certified") or {}).items():
        cv = _cert_value(str(element), spec, default_type, origin)
        if cv is None:
            unfilled.append(str(element))
        else:
            certified[str(element)] = cv
    if not certified:
        raise ValueError(
            f"{origin}: CRM has no usable values"
            + (f" — {len(unfilled)} element(s) are placeholders awaiting "
               f"transcription; keep the file as *.yaml.example until it has at "
               f"least one value" if unfilled else ""))
    if not data.get("unit"):
        raise ValueError(f"{origin}: CRM must declare a 'unit' for its values")

    prov = data.get("provenance") or {}
    patterns = (data.get("match") or {}).get("name_patterns") or []
    return CRM(
        id=str(data.get("id") or Path(origin).stem),
        name=str(data.get("name") or data.get("id") or Path(origin).stem),
        unit=str(data["unit"]),
        certified=certified,
        patterns=[re.compile(str(p)) for p in patterns],
        matrix=str(data.get("matrix", "")),
        source=str(data.get("source", "")),
        note=str(data.get("note", "")),
        provenance=Provenance(
            compilation=str(prov.get("compilation", "")),
            version=str(prov.get("version", "")),
            accessed=str(prov.get("accessed", "")),
            citation=str(prov.get("citation", "")),
        ),
        unfilled=unfilled,
    )


def resolve_library_dir(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_dir():
        return p
    for base in (_REPO_CONFIG_DIR, Path.cwd() / "configs"):
        if (base / name_or_path).is_dir():
            return base / name_or_path
    raise FileNotFoundError(
        f"CRM library '{name_or_path}' not found in {_REPO_CONFIG_DIR} or ./configs")


def load_library(name_or_path: str = "crm") -> list[CRM]:
    """Load every *.yaml in the library directory. A broken file is fatal, loudly.

    Skipping an unreadable CRM file would silently drop the material a reviewer
    believes was checked — the same failure mode as a check that stays quiet.
    """
    out = []
    for path in sorted(resolve_library_dir(name_or_path).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: not a YAML mapping")
        out.append(_crm_from_dict(data, str(path)))
    return out


def match_sample(library: list[CRM], sample_name: str) -> CRM | None:
    """First CRM whose patterns claim this sample name, or None."""
    for ref in library:
        if ref.matches(sample_name):
            return ref
    return None
