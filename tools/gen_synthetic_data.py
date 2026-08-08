#!/usr/bin/env python3
"""Generate a synthetic MassHunter-style wide batch export for demos and tests.

Entirely fabricated data (seeded RNG) in the "masshunter_quant_wide" reference
layout — no real instrument output involved. The default batch is fully passing
(including CCV cadence: a CCV/CCB pair after every 10 analyses and at the end).
Both modes consume an identical RNG stream — --violations only overrides the
injected values, so everything else matches the passing batch exactly.
--violations injects known QC failures so golden tests have both polarities:

  * mid-run CCV at ~85% recovery            (fails a 90-110% window)
  * ISTD drift to ~65% on the last samples  (fails a 70-130% window)
  * duplicate pushed to ~30% RPD            (fails RPD <= 20%)

Usage:
  python tools/gen_synthetic_data.py demo_batch.csv [--violations] [--seed 42]
"""
from __future__ import annotations

import argparse
import csv
import math
import random

ANALYTES = ["9 Be", "52 Cr [He]", "60 Ni [He]", "63 Cu [He]",
            "66 Zn [He]", "75 As [He]", "111 Cd", "208 Pb"]
ISTDS = {"45 Sc": 350_000.0, "115 In": 500_000.0, "209 Bi": 420_000.0}
SENS_CPS_PER_PPB = 2_000.0
CAL_LEVELS = [1.0, 5.0, 10.0, 50.0, 100.0]
SPIKE_PPB = 25.0

#: A fabricated "certified" reference material, one value per element, mirroring
#: configs/crm/example_synthetic_water.yaml. A real CRM certifies every element at
#: a different value — which is exactly why it cannot be expressed as a single
#: Level column, and why crm_recovery reads the values from the library instead.
CRM_NAME = "CRM-EXAMPLE-1"
CRM_CERTIFIED = {"9 Be": 20.0, "52 Cr [He]": 40.0, "60 Ni [He]": 30.0,
                 "63 Cu [He]": 60.0, "66 Zn [He]": 80.0, "75 As [He]": 15.0,
                 "111 Cd": 10.0, "208 Pb": 25.0}


def _noise(rng: random.Random, sd: float = 0.02) -> float:
    return rng.gauss(1.0, sd)


def _row(rng, seq, name, stype, level, conc_by_analyte, istd_scale=1.0):
    row = {"Seq": seq, "Sample Name": name, "Type": stype,
           "Level [ppb]": f"{level:g}" if level is not None else ""}
    for a in ANALYTES:
        conc = conc_by_analyte.get(a, 0.0)
        # Blank scatter is deliberately well under the 0.1 ppb LOQ the rule packs
        # configure, so blank_derived_lod's 10-sigma estimate clears it — i.e. the
        # reference batch depicts a run whose reporting limit is defensible.
        measured = conc * _noise(rng) if conc > 0 else abs(rng.gauss(0.005, 0.003))
        row[f"{a} Conc. [ppb]"] = f"{measured:.4f}"
        cps = measured * SENS_CPS_PER_PPB * _noise(rng)
        row[f"{a} CPS"] = f"{cps:.0f}"
        # Counting statistics alone set a floor on precision: RSD ~ 100/sqrt(counts).
        # Derived, not drawn, so both batches keep consuming an identical RNG stream —
        # and a blank comes out genuinely imprecise, which is what gating is for.
        row[f"{a} CPS RSD"] = f"{max(0.3, 100.0 / math.sqrt(max(cps, 1.0))):.2f}"
    for istd, base in ISTDS.items():
        row[f"{istd} CPS (ISTD)"] = f"{base * istd_scale * _noise(rng):.0f}"
    return row


def generate(path: str, violations: bool = False, seed: int = 42) -> int:
    rng = random.Random(seed)
    rows = []
    seq = 1

    def emit(name, stype, level=None, conc=None, istd_scale=1.0):
        nonlocal seq
        rows.append(_row(rng, seq, name, stype, level, conc or {}, istd_scale))
        seq += 1

    def flat(v):
        return {a: v for a in ANALYTES}

    # --- calibration block (not counted as analyses) -------------------------
    emit("Cal Blank", "CalBlk")
    for lvl in CAL_LEVELS:
        emit(f"Cal Std {lvl:g} ppb", "CalStd", level=lvl, conc=flat(lvl))
    emit("ICV-50", "ICV", level=50.0, conc=flat(50.0 * rng.uniform(0.97, 1.03)))
    emit("ICB", "ICB")

    # --- analysis segment 1: exactly 10 analyses, then CCV/CCB ---------------
    emit("MB-1", "MB")
    emit("LCS-25", "LCS", level=SPIKE_PPB, conc=flat(SPIKE_PPB * rng.uniform(0.95, 1.05)))

    sample_concs = {}
    for i in range(1, 6):                                   # S001..S005
        name = f"S{i:03d}"
        sample_concs[name] = {a: rng.uniform(0.5, 80.0) for a in ANALYTES}
        emit(name, "Sample", conc=sample_concs[name])
        if i == 3:                                          # duplicate pair on S003
            # always draw the noise so both modes consume one identical RNG
            # stream; --violations only overrides the injected values
            dup = {}
            for a, v in sample_concs[name].items():
                noise = rng.uniform(0.97, 1.03)
                dup[a] = v * (1.30 if violations else noise)
            emit("S003-DUP", "Dup", conc=dup)
        if i == 5:                                          # correlated MS/MSD on S005
            pair_rec = rng.uniform(0.88, 1.08)
            for suffix, stype in (("-MS", "MS"), ("-MSD", "MSD")):
                spiked = {a: v + SPIKE_PPB * pair_rec * _noise(rng)
                          for a, v in sample_concs[name].items()}
                emit(f"S005{suffix}", stype, level=SPIKE_PPB, conc=spiked)

    ccv_scale = rng.uniform(0.97, 1.03)
    if violations:
        ccv_scale = 0.85
    emit("CCV-50 #1", "CCV", level=50.0, conc=flat(50.0 * ccv_scale))
    emit("CCB #1", "CCB")

    # --- analysis segment 2: exactly 10 analyses, then closing CCV/CCB -------
    for i in range(6, 15):                                  # S006..S014
        istd_scale = rng.uniform(0.90, 1.0)
        if violations and i >= 13:
            istd_scale = 0.65
        emit(f"S{i:03d}", "Sample",
             conc={a: rng.uniform(0.5, 80.0) for a in ANALYTES}, istd_scale=istd_scale)

    # 10th analysis of the segment: the reference material. Its ISTD draw happens
    # in both modes (identical RNG stream) but is never overridden — the injected
    # ISTD drift stays attributable to the S0xx samples alone.
    crm_istd = rng.uniform(0.90, 1.0)
    emit(CRM_NAME, "LCS", conc=dict(CRM_CERTIFIED), istd_scale=crm_istd)

    emit("CCV-50 #2", "CCV", level=50.0, conc=flat(50.0 * rng.uniform(0.97, 1.03)))
    emit("CCB #2", "CCB")

    # --- write ---------------------------------------------------------------
    fieldnames = (["Seq", "Sample Name", "Type", "Level [ppb]"]
                  + [f"{a} Conc. [ppb]" for a in ANALYTES]
                  + [f"{a} CPS" for a in ANALYTES]
                  + [f"{a} CPS RSD" for a in ANALYTES]
                  + [f"{i} CPS (ISTD)" for i in ISTDS])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_csv")
    ap.add_argument("--violations", action="store_true",
                    help="inject known QC failures (CCV, ISTD drift, dup RPD)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    n = generate(args.out_csv, violations=args.violations, seed=args.seed)
    mode = "with injected violations" if args.violations else "all-passing"
    print(f"wrote {n} rows ({mode}) -> {args.out_csv}")


if __name__ == "__main__":
    main()
