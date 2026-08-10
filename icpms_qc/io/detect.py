"""Work out what a file is, so nobody has to be told.

Asking a user which template matches their export is asking them to know the
answer before the tool has done anything for them. Every shipped layout is tried
instead, and the one that reads the file best wins — where "best" is measured,
not guessed: how many analytes it recovered, how many columns it could not place,
how many rows fell through to an unknown sample type.

The same applies to the companion counts export. A lab that wants both
concentrations and raw counts exports the batch twice, and the two files sit next
to each other with matching names. If the partner is right there, use it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from icpms_qc.io import element, masshunter
from icpms_qc.model import Batch, SampleType

#: Tried in order; ties break toward the earlier entry.
MASSHUNTER_TEMPLATES = ("masshunter_conc_2row", "masshunter_counts_2row",
                        "masshunter_quant_wide")

#: How a paired export names itself relative to its partner.
_PAIRS = (("count", "conc"), ("conc", "count"),
          ("Count", "Conc"), ("Conc", "Count"),
          ("COUNT", "CONC"), ("CONC", "COUNT"))


@dataclass
class Detection:
    batch: Batch
    reader: str                      # "masshunter" | "element"
    template: str
    companion: str | None = None     # counts export merged in, if one was found

    def describe(self) -> str:
        bits = [f"{self.reader} · {self.template}"]
        if self.companion:
            bits.append(f"paired with {Path(self.companion).name}")
        return " · ".join(bits)


def _score(batch: Batch) -> tuple[int, int, int]:
    """Higher is better: analytes found, then fewer unmapped, fewer unknown types."""
    unmapped = sum(1 for w in batch.warnings if w.startswith("unmapped column"))
    unknown = sum(1 for s in batch.samples if s.type is SampleType.OTHER)
    return (len(batch.analytes), -unmapped, -unknown)


def find_companion_counts(path: str) -> str | None:
    """Find the counts export belonging to this concentration export.

    Three naming habits show up across a facility, sometimes in one folder:
    swapped (`Conc_0816` / `Count_0816`), differently cased (`CONC` / `COUNT`),
    and appended, where the concentration file is the counts filename with a
    suffix bolted on (`airfilter_count_0710_conc` beside `airfilter_count_0710`).
    The last one defeats a straight substitution, because both words are in the
    same name.
    """
    p = Path(path)
    stem = p.stem

    # Appended form first: the partner's name is this one minus the suffix, and
    # substituting inside it would mangle the counts word that is already there.
    for word in ("conc", "concentration"):
        for sep in ("_", "-", " "):
            tail = f"{sep}{word}"
            if stem.lower().endswith(tail):
                candidate = p.with_name(stem[: -len(tail)] + p.suffix)
                if candidate.exists() and candidate != p:
                    return str(candidate)

    for a, b in _PAIRS:
        if a in stem:
            candidate = p.with_name(p.name.replace(a, b, 1))
            if candidate.exists() and candidate != p:
                return str(candidate)
    return None


def detect(path: str, rules_hint: str | None = None) -> Detection:
    """Read `path` with whatever fits it, and say what that turned out to be."""
    target = Path(path)

    if target.is_dir():
        return Detection(element.parse_folder(str(target)), "element", "element_ascii")

    if target.suffix.lower() == ".asc":
        try:
            return Detection(element.parse(str(target)), "element", "element_ascii")
        except ValueError:
            sample, analytes, _ = element.parse_sample_file(str(target))
            b = Batch(source_path=str(target), template_id="element_ascii",
                      instrument_family="thermo-element")
            b.samples, b.analytes, b.flags_column_mapped = [sample], analytes, True
            return Detection(b, "element", "element_ascii (single sample)")

    best: tuple[tuple[int, int, int], Batch, str] | None = None
    errors: list[str] = []
    for name in MASSHUNTER_TEMPLATES:
        try:
            batch = masshunter.parse(str(target), template=name)
        except (ValueError, OSError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        score = _score(batch)
        if best is None or score > best[0]:
            best = (score, batch, name)

    if best is None:
        raise ValueError(
            f"{target.name}: no shipped layout could read this file.\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n\nRun `icpms-qc inspect` to see the layout, then "
              "`icpms-qc template-from-header` to draft a template for it.")

    _, batch, name = best
    found = Detection(batch, "masshunter", name)

    # Only worth pairing if this file lacks intensities and its partner has them.
    has_cps = any(r.intensity is not None
                  for s in batch.samples for r in s.results.values())
    if not has_cps and (mate := find_companion_counts(str(target))):
        try:
            masshunter.attach_intensities(batch, mate)
            found.companion = mate
        except (ValueError, OSError):
            pass
    return found
