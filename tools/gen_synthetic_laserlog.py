#!/usr/bin/env python3
"""Generate a synthetic laser ablation log in the iolite/NWL CSV layout.

Entirely fabricated — no instrument, no facility, no real run. Exists so the test
suite and the demo can exercise laser_log_alignment without anyone's data.

The layout mirrors what the laser software writes: one row per *event*, not per
ablation. `Laser State` toggles On/Off around each firing; the Off rows between
them are stage moves. `Sequence Number` and `Comment` appear only on the first row
of a sequence and are blank after it — the reader forward-fills them.

Usage:
  python tools/gen_synthetic_laserlog.py log.csv [--samples S001,S002] [--spots 5]
  python tools/gen_synthetic_laserlog.py log.csv --drop-sequence 3   # inject a gap
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta

COLUMNS = ["Timestamp", "Sequence Number", "SubPoint Number", "Vertex Number",
           "Comment", "X(um)", "Y(um)", "Intended X(um)", "Intended Y(um)",
           "Scan Velocity (um/s)", "Laser State", "Laser Rep. Rate (Hz)",
           "Spot Type", "Spot Size (um)", "Spot Angle (deg)", "MFC1 (ml/min)",
           "MFC2 (ml/min)", "Cell Pressure (kPa)", "Z(um)"]

START = datetime(2026, 8, 3, 10, 0, 0)


def generate(path: str, samples: list[str], spots: int = 5,
             ablation_s: float = 2.5, gap_s: float = 1.5,
             drop_sequence: int | None = None,
             short_ablation: tuple[int, float] | None = None) -> int:
    """Write a log of len(samples) sequences x `spots` ablations each.

    drop_sequence   omit that 1-based sequence entirely (simulates a lost trigger)
    short_ablation  (index, seconds) force one ablation to an odd duration
    """
    rows: list[dict] = []
    t = START
    x, y = 8500.0, 34800.0
    abl = 0

    for si, name in enumerate(samples, start=1):
        if si == drop_sequence:
            continue
        first_of_sequence = True
        for k in range(spots):
            abl += 1
            dur = ablation_s
            if short_ablation and short_ablation[0] == abl:
                dur = short_ablation[1]

            def row(state: str, at: datetime, **over) -> dict:
                r = dict.fromkeys(COLUMNS, "")
                r.update({
                    "Timestamp": at.strftime("%Y-%m-%d %H:%M:%S.") + f"{at.microsecond // 1000:03d}",
                    "X(um)": f"{x:.4f}", "Y(um)": f"{y + k * 40:.4f}",
                    "Laser State": state, "Laser Rep. Rate (Hz)": "200" if state == "On" else "0",
                    "Spot Size (um)": "40 x 40",
                    "MFC1 (ml/min)": "0.900", "MFC2 (ml/min)": "0.600",
                    "Cell Pressure (kPa)": "101.3",
                })
                r.update(over)
                return r

            if first_of_sequence:
                # Sequence header: the only row carrying the number and the name.
                rows.append(row("Off", t, **{"Sequence Number": str(si),
                                             "SubPoint Number": "1", "Comment": name}))
                t += timedelta(seconds=0.5)
                first_of_sequence = False

            rows.append(row("On", t))
            t += timedelta(seconds=dur)
            rows.append(row("Off", t))
            t += timedelta(seconds=gap_s)

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return abl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_csv")
    ap.add_argument("--samples", default="S001,S002,S003",
                    help="comma-separated sequence comments (sample names)")
    ap.add_argument("--spots", type=int, default=5)
    ap.add_argument("--drop-sequence", type=int, default=None,
                    help="omit this 1-based sequence, simulating a lost trigger")
    args = ap.parse_args()
    n = generate(args.out_csv, args.samples.split(","), spots=args.spots,
                 drop_sequence=args.drop_sequence)
    print(f"wrote {n} ablations -> {args.out_csv}")


if __name__ == "__main__":
    main()
