"""Canonical batch model — the only thing the QC engine ever sees.

Parsers (icpms_qc.io.*) translate vendor export layouts into these dataclasses via
template mappings; checks (icpms_qc.qc.*) consume them. Nothing downstream of io/
may touch raw CSV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SampleType(str, Enum):
    CAL_STD = "CAL_STD"
    CAL_BLANK = "CAL_BLANK"
    ICV = "ICV"
    CCV = "CCV"
    ICB = "ICB"
    CCB = "CCB"
    MB = "MB"                  # method/prep blank
    LCS = "LCS"                # lab control sample
    SAMPLE = "SAMPLE"
    DUP = "DUP"
    MS = "MS"                  # matrix spike
    MSD = "MSD"
    SERIAL_DIL = "SERIAL_DIL"
    POST_SPIKE = "POST_SPIKE"
    OTHER = "OTHER"            # unrecognized — parser must warn, never guess


#: sample types that count as "analyses" for QC-frequency purposes
ANALYSIS_TYPES = {SampleType.MB, SampleType.LCS, SampleType.SAMPLE, SampleType.DUP,
                  SampleType.MS, SampleType.MSD, SampleType.SERIAL_DIL, SampleType.POST_SPIKE}


@dataclass
class Analyte:
    label: str                 # e.g. "75 As [He]" — exactly as exported
    mass: int | None = None
    element: str | None = None
    istd_label: str | None = None
    #: Q2 mass on a triple-quad (8800/8900) run in MS/MS mode — "31 -> 47 P [O2]"
    #: measures P at m/z 31 in Q1 and its O2 adduct at 47 in Q2. None on
    #: single-quad instruments and on QQQ runs exported without the shift.
    mass_shift: int | None = None
    #: collision/reaction cell mode as exported — "He", "No Gas", "O2", "HEHe".
    #: Whitespace-collapsed but otherwise verbatim: the same cell gas is spelled
    #: differently across software versions, and inventing a canonical spelling
    #: would silently merge two columns a lab deliberately keeps apart.
    mode: str | None = None

    @property
    def is_msms(self) -> bool:
        """True for a triple-quad MS/MS acquisition (Q1 and Q2 both set)."""
        return self.mass_shift is not None

    @property
    def key(self) -> str:
        """Element+mass+mode identity, for grouping the same element across modes."""
        parts = [self.element or "?", str(self.mass or "?")]
        if self.mass_shift is not None:
            parts.append(f"->{self.mass_shift}")
        if self.mode:
            parts.append(f"[{self.mode}]")
        return " ".join(parts)


@dataclass
class Result:
    conc: float | None         # None = not reported for this sample/analyte
    unit: str = "ppb"
    intensity: float | None = None
    istd_intensity: float | None = None
    flags: list[str] = field(default_factory=list)
    #: The export said "<0.05" or "ND" — the analyte was measured and not
    #: detected. Distinct from conc=None, which means nothing was reported at
    #: all. Conflating the two makes a clean blank look like a missing one.
    below_dl: bool = False
    #: The detection limit quoted alongside a censored result ("<0.05" -> 0.05).
    #: None when the export censored without a number ("ND").
    dl: float | None = None
    #: Relative standard deviation of the replicate integrations, in percent —
    #: the export's own precision figure for this measurement. Accuracy checks
    #: cannot see it, and on a research batch it is often the only thing wrong.
    rsd_pct: float | None = None

    @property
    def upper_bound(self) -> float | None:
        """Largest concentration consistent with this result, or None if unknown.

        A censored result is not a measurement but it is not nothing either: it
        bounds the truth from above, which is enough to decide a blank check.
        """
        return self.dl if (self.below_dl and self.conc is None) else self.conc


@dataclass
class InstrumentFlag:
    """One QC objection raised by the instrument software itself.

    The vendor already judged this run and wrote its verdict into the export. A
    tool that calls itself auditable and then discards that verdict is hiding
    evidence — so it is parsed, carried, and reported alongside icpms-qc's own.
    """
    analyte: str                  # label as the instrument wrote it
    metric: str                   # e.g. "CPS RSD"
    value: float | None = None
    limit: float | None = None
    #: "high" when the value exceeded a maximum, "low" when it fell under a
    #: minimum. The instrument objects in both directions and the report must
    #: not render a calibration R below 0.95 as though it were too large.
    direction: str = "high"
    text: str = ""                # the original sentence, kept verbatim

    def describe(self) -> str:
        if self.value is None or self.limit is None:
            return f"{self.analyte}: {self.metric}"
        sign = ">" if self.direction == "high" else "<"
        return f"{self.analyte}: {self.metric} {self.value:g} {sign} {self.limit:g}"


@dataclass
class Sample:
    name: str
    seq_index: int
    type: SampleType
    level: float | None = None            # expected conc for CAL_STD/ICV/CCV/LCS/spikes
    parent_name: str | None = None        # DUP/MS/MSD/SERIAL_DIL → their parent sample
    results: dict[str, Result] = field(default_factory=dict)   # analyte label → Result
    istd_intensities: dict[str, float] = field(default_factory=dict)  # istd label → CPS
    flags: list[str] = field(default_factory=list)  # instrument-software QC flag text
    #: `flags` parsed into structure where the wording allowed it
    instrument_flags: list[InstrumentFlag] = field(default_factory=list)
    #: Dilution applied before measurement, when the export states it. Most do
    #: not, which is why a recomputed concentration can only ever be compared in
    #: the vial rather than in the original sample.
    dilution_factor: float | None = None


@dataclass
class Batch:
    source_path: str
    template_id: str
    instrument_family: str                # "agilent-masshunter" for v0.1
    analytes: list[Analyte] = field(default_factory=list)
    istds: list[Analyte] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Did the template map a column carrying the instrument's own QC text?
    #: "The instrument raised no objection" and "we never read its objections"
    #: are both an empty list, and must never be reported as the same thing.
    flags_column_mapped: bool = False

    def of_type(self, *types: SampleType) -> list[Sample]:
        return [s for s in self.samples if s.type in types]

    def find_sample(self, name: str, *types: SampleType) -> Sample | None:
        for s in self.samples:
            if s.name == name and (not types or s.type in types):
                return s
        return None
