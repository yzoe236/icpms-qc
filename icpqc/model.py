"""Canonical batch model — the only thing the QC engine ever sees.

Parsers (icpqc.io.*) translate vendor export layouts into these dataclasses via
template mappings; checks (icpqc.qc.*) consume them. Nothing downstream of io/
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


@dataclass
class Result:
    conc: float | None         # None = not reported for this sample/analyte
    unit: str = "ppb"
    intensity: float | None = None
    istd_intensity: float | None = None
    flags: list[str] = field(default_factory=list)


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


@dataclass
class Batch:
    source_path: str
    template_id: str
    instrument_family: str                # "agilent-masshunter" for v0.1
    analytes: list[Analyte] = field(default_factory=list)
    istds: list[Analyte] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def of_type(self, *types: SampleType) -> list[Sample]:
        return [s for s in self.samples if s.type in types]

    def find_sample(self, name: str, *types: SampleType) -> Sample | None:
        for s in self.samples:
            if s.name == name and (not types or s.type in types):
                return s
        return None
