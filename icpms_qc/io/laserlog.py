"""Laser ablation log (iolite/NWL-style CSV) → LaserLog.

Why icpms-qc reads this at all
---------------------------
The laser and the mass spectrometer are two instruments with two clocks, started
by two computers. The laser log knows *when it fired and where*; the ICP data
knows *what it counted*. Nothing in either file states which counts belong to
which ablation — that correspondence is reconstructed downstream, and when it is
reconstructed wrongly every number after it is wrong while the report stays green.

icpms-qc does not perform that alignment: segmenting a transient signal is reduction,
and reduction belongs to the tools that already do it well (pewpew/pewlib, Ilaps,
iolite, laserTRAM). What no tool does is *audit* the result. This module exists to
make the audit possible — it turns the laser's own record of the run into
something the QC engine can compare the reduced results against.

The format
----------
One row per laser event, not per ablation. Columns::

    Timestamp, Sequence Number, SubPoint Number, Vertex Number, Comment,
    X(um), Y(um), Intended X(um), Intended Y(um), Scan Velocity (um/s),
    Laser State, Laser Rep. Rate (Hz), Spot Type, Spot Size (um),
    Spot Angle (deg), MFC1 (ml/min), MFC2 (ml/min), Cell Pressure (kPa), Z(um)

Two properties drive the parser:

* ``Laser State`` toggles On/Off. One ablation is one On→Off span; the many Off
  rows in between are stage moves, which is why row counts and ablation counts
  are nothing like each other.
* ``Sequence Number`` and ``Comment`` appear **only on the first row of each
  sequence** and are blank thereafter, so they must be carried forward. A
  sequence is one pattern — a raster image or a group of spots — and carries the
  sample name; the ablations inside it are its lines or spots.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime

_TS_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
               "%d/%m/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S.%f")

#: Column names as written by the laser software. Kept verbatim so a log can be
#: recognized by its header rather than by position.
TIMESTAMP = "Timestamp"
SEQUENCE = "Sequence Number"
COMMENT = "Comment"
STATE = "Laser State"


def _ts(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _f(raw: str | None) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Ablation:
    """One On→Off span: the laser was actually firing."""
    index: int                       # 1-based over the whole log
    sequence: int | None
    comment: str
    start: datetime
    end: datetime | None             # None = log ended mid-ablation
    x: float | None = None
    y: float | None = None
    spot_size: str = ""

    @property
    def duration_s(self) -> float | None:
        if self.end is None:
            return None
        return (self.end - self.start).total_seconds()


@dataclass
class Sequence:
    """One pattern — a raster image or a spot group — carrying the sample name."""
    number: int | None
    comment: str
    ablations: list[Ablation] = field(default_factory=list)

    @property
    def start(self) -> datetime | None:
        return self.ablations[0].start if self.ablations else None

    @property
    def end(self) -> datetime | None:
        ends = [a.end for a in self.ablations if a.end is not None]
        return max(ends) if ends else None


@dataclass
class EnvPoint:
    """Timestamped cell environment — carrier gas and pressure."""
    at: datetime
    mfc1: float | None = None
    mfc2: float | None = None
    cell_pressure: float | None = None


@dataclass
class LaserLog:
    source_path: str
    ablations: list[Ablation] = field(default_factory=list)
    sequences: list[Sequence] = field(default_factory=list)
    environment: list[EnvPoint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def start(self) -> datetime | None:
        return self.ablations[0].start if self.ablations else None

    @property
    def end(self) -> datetime | None:
        return self.sequences[-1].end if self.sequences else None


def looks_like_laser_log(path: str) -> bool:
    """Cheap header sniff, so the CLI can refuse a results file by mistake."""
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            header = next(csv.reader(fh), [])
    except OSError:
        return False
    cols = {c.strip() for c in header}
    return TIMESTAMP in cols and STATE in cols


def parse(path: str) -> LaserLog:
    """Read a laser log into ablations grouped by sequence."""
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh, skipinitialspace=True))

    log = LaserLog(source_path=str(path))
    if not rows:
        raise ValueError(f"{path}: laser log is empty")
    if STATE not in rows[0] or TIMESTAMP not in rows[0]:
        raise ValueError(
            f"{path}: not a laser log — expected '{TIMESTAMP}' and '{STATE}' columns, "
            f"found {', '.join(list(rows[0])[:6])}")

    by_sequence: dict[tuple, Sequence] = {}
    cur_seq: int | None = None
    cur_comment = ""
    prev_state = "off"
    open_ablation: Ablation | None = None
    bad_timestamps = 0

    for row in rows:
        # Sparse columns: a blank cell means "still the previous one", not "none".
        if (s := (row.get(SEQUENCE) or "").strip()):
            cur_seq = int(s) if s.isdigit() else None
        if (c := (row.get(COMMENT) or "").strip()):
            cur_comment = c

        at = _ts(row.get(TIMESTAMP, ""))
        if at is None:
            bad_timestamps += 1
            continue

        if any(row.get(k) for k in ("MFC1 (ml/min)", "MFC2 (ml/min)",
                                    "Cell Pressure (kPa)")):
            log.environment.append(EnvPoint(
                at, _f(row.get("MFC1 (ml/min)")), _f(row.get("MFC2 (ml/min)")),
                _f(row.get("Cell Pressure (kPa)"))))

        state = (row.get(STATE) or "").strip().lower()
        if state == "on" and prev_state != "on":
            open_ablation = Ablation(
                index=len(log.ablations) + 1, sequence=cur_seq, comment=cur_comment,
                start=at, end=None, x=_f(row.get("X(um)")), y=_f(row.get("Y(um)")),
                spot_size=(row.get("Spot Size (um)") or "").strip())
            log.ablations.append(open_ablation)
            key = (cur_seq, cur_comment)
            if key not in by_sequence:
                by_sequence[key] = Sequence(cur_seq, cur_comment)
                log.sequences.append(by_sequence[key])
            by_sequence[key].ablations.append(open_ablation)
        elif state != "on" and open_ablation is not None:
            open_ablation.end = at
            open_ablation = None
        prev_state = state

    if open_ablation is not None:
        log.warnings.append(
            f"log ends while the laser is still On (ablation #{open_ablation.index}) — "
            f"the file is truncated or the run was interrupted")
    if bad_timestamps:
        log.warnings.append(f"{bad_timestamps} row(s) had an unreadable timestamp")
    if not log.ablations:
        log.warnings.append("no ablations found — the laser never reported State=On")
    return log
