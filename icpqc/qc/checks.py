"""Check functions — one small pure function per catalog entry (SPEC §4).

Contract for every check:
  * input: Batch + params dict (rule pack `shared:` merged with the check's
    own `params:`, check-specific wins); never raw CSV
  * output: CheckResult with outcome PASS / FAIL / WARN / NOT_EVALUATED(reason)
  * a check that cannot run (missing QC sample type) returns NOT_EVALUATED with
    the reason — silence is how QC reports lie
  * thresholds always come from params; no numeric literals in check bodies
  * detail rows use `ok`: True (pass) / False (fail) / None (informational)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from icpqc.model import ANALYSIS_TYPES, Batch, SampleType


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass
class CheckResult:
    check_id: str
    outcome: Outcome
    reason: str = ""
    verify: str = ""                                    # rule-pack verify note
    details: list[dict] = field(default_factory=list)   # per-analyte / per-sample rows


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _ne(check_id: str, reason: str) -> CheckResult:
    return CheckResult(check_id, Outcome.NOT_EVALUATED, reason=reason)


def _finish(check_id: str, details: list[dict],
            warn_reason: str = "evaluated, but no decisive rows") -> CheckResult:
    fails = [d for d in details if d.get("ok") is False]
    decided = [d for d in details if d.get("ok") is not None]
    if fails:
        return CheckResult(check_id, Outcome.FAIL,
                           reason=f"{len(fails)} failing row(s)", details=details)
    if not decided:
        return CheckResult(check_id, Outcome.WARN, reason=warn_reason, details=details)
    return CheckResult(check_id, Outcome.PASS, details=details)


def _window(params: dict, default: tuple[float, float]) -> tuple[float, float]:
    lo, hi = params.get("window_pct", list(default))
    return float(lo), float(hi)


def _loq_for(label: str, params: dict) -> float:
    loq = params.get("loq_ppb", 0.1)
    if isinstance(loq, dict):
        return float(loq.get(label, loq.get("default", 0.1)))
    return float(loq)


def _blank_limit(label: str, params: dict) -> float:
    limit = params.get("limit", "LOQ")
    if isinstance(limit, (int, float)):
        return float(limit)
    key = str(limit).upper().replace(" ", "")
    loq = _loq_for(label, params)
    if key == "LOQ":
        return loq
    if key in {"1/2LOQ", "0.5LOQ", "HALFLOQ"}:
        return loq / 2.0
    raise ValueError(f"unrecognized blank limit {limit!r} (use 'LOQ', '1/2LOQ' or a number)")


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / (sxx * syy) ** 0.5


def _recovery_details(samples, batch: Batch, lo: float, hi: float) -> list[dict]:
    details: list[dict] = []
    for s in samples:
        if not s.level:
            details.append({"sample": s.name, "analyte": "-", "recovery_pct": None,
                            "window": f"{lo:g}-{hi:g}%", "ok": None,
                            "note": "no expected level in export"})
            continue
        for a in batch.analytes:
            r = s.results.get(a.label)
            if r is None or r.conc is None:
                details.append({"sample": s.name, "analyte": a.label, "recovery_pct": None,
                                "window": f"{lo:g}-{hi:g}%", "ok": None, "note": "no result"})
                continue
            rec = r.conc / s.level * 100.0
            details.append({"sample": s.name, "analyte": a.label,
                            "recovery_pct": round(rec, 1),
                            "window": f"{lo:g}-{hi:g}%", "ok": lo <= rec <= hi})
    return details


def _blank_details(samples, batch: Batch, params: dict) -> list[dict]:
    details: list[dict] = []
    for s in samples:
        for a in batch.analytes:
            r = s.results.get(a.label)
            conc = r.conc if r else None
            if conc is None:
                details.append({"sample": s.name, "analyte": a.label, "conc_ppb": None,
                                "limit_ppb": None, "ok": None, "note": "no result"})
                continue
            thr = _blank_limit(a.label, params)
            details.append({"sample": s.name, "analyte": a.label,
                            "conc_ppb": round(conc, 4), "limit_ppb": thr,
                            "ok": abs(conc) < thr})
    return details


# --------------------------------------------------------------------------- #
# v0.1 catalog (SPEC §4)
# --------------------------------------------------------------------------- #

def cal_linearity(batch: Batch, params: dict) -> CheckResult:
    """Calibration correlation coefficient per analyte.

    Post-hoc proxy: computed on measured conc vs nominal level of the cal
    standards present in the export (raw cal signals are not exported).
    """
    stds = [s for s in batch.of_type(SampleType.CAL_STD) if s.level]
    if len(stds) < 3:
        return _ne("cal_linearity", f"need >=3 cal standards with levels, found {len(stds)}")
    min_r = float(params.get("min_r", 0.998))
    details = []
    for a in batch.analytes:
        pts = [(s.level, s.results[a.label].conc) for s in stds
               if s.results.get(a.label) and s.results[a.label].conc is not None]
        if len(pts) < 3:
            details.append({"analyte": a.label, "n": len(pts), "r": None,
                            "min_r": min_r, "ok": None, "note": "insufficient points"})
            continue
        r = _pearson([p[0] for p in pts], [p[1] for p in pts])
        details.append({"analyte": a.label, "n": len(pts), "r": round(r, 5),
                        "min_r": min_r, "ok": r >= min_r})
    return _finish("cal_linearity", details)


def cal_low_std(batch: Batch, params: dict) -> CheckResult:
    """Lowest non-zero calibration standard recovers within window."""
    stds = [s for s in batch.of_type(SampleType.CAL_STD) if s.level]
    if not stds:
        return _ne("cal_low_std", "no cal standards with levels")
    low = min(stds, key=lambda s: s.level)
    lo, hi = _window(params, (70, 130))
    return _finish("cal_low_std", _recovery_details([low], batch, lo, hi))


def icv_recovery(batch: Batch, params: dict) -> CheckResult:
    """Initial calibration verification (second source) recovery."""
    icvs = batch.of_type(SampleType.ICV)
    if not icvs:
        return _ne("icv_recovery", "no ICV samples in batch")
    lo, hi = _window(params, (90, 110))
    return _finish("icv_recovery", _recovery_details(icvs, batch, lo, hi))


def ccv_recovery(batch: Batch, params: dict) -> CheckResult:
    """Continuing calibration verification recovery — every CCV in the run."""
    ccvs = batch.of_type(SampleType.CCV)
    if not ccvs:
        return _ne("ccv_recovery", "no CCV samples in batch")
    lo, hi = _window(params, (90, 110))
    return _finish("ccv_recovery", _recovery_details(ccvs, batch, lo, hi))


def ccv_frequency(batch: Batch, params: dict) -> CheckResult:
    """CCV cadence: at most `every_n` analyses between CCVs; run ends on a CCV."""
    if not batch.of_type(SampleType.CCV):
        return _ne("ccv_frequency", "no CCV samples in batch")
    every_n = int(params.get("every_n", 10))
    end_required = bool(params.get("end_of_run", True))
    details, run, seg = [], 0, 1
    for s in batch.samples:
        if s.type == SampleType.CCV:
            details.append({"segment": seg, "analyses_since_last_ccv": run,
                            "limit": every_n, "ok": run <= every_n})
            seg, run = seg + 1, 0
        elif s.type in ANALYSIS_TYPES:
            run += 1
    if run > 0:
        details.append({"segment": seg, "analyses_since_last_ccv": run, "limit": every_n,
                        "ok": False if end_required else None,
                        "note": "analyses after the last CCV"
                                + (" (end-of-run CCV required)" if end_required else "")})
    return _finish("ccv_frequency", details)


def icb_ccb_blank(batch: Batch, params: dict) -> CheckResult:
    """Calibration blanks (ICB/CCB) below the reporting threshold."""
    blanks = batch.of_type(SampleType.ICB, SampleType.CCB)
    if not blanks:
        return _ne("icb_ccb_blank", "no ICB/CCB samples in batch")
    return _finish("icb_ccb_blank", _blank_details(blanks, batch, params))


def method_blank(batch: Batch, params: dict) -> CheckResult:
    """Method/prep blanks below the reporting threshold."""
    mbs = batch.of_type(SampleType.MB)
    if not mbs:
        return _ne("method_blank", "no method blank in batch")
    return _finish("method_blank", _blank_details(mbs, batch, params))


def istd_recovery(batch: Batch, params: dict) -> CheckResult:
    """ISTD intensity of every post-calibration sample vs the ICAL reference.

    Reference = mean ISTD intensity across the calibration block (CAL_STD +
    CAL_BLANK). Details: one summary row per ISTD plus every failing sample.
    """
    if not batch.istds:
        return _ne("istd_recovery", "no ISTD columns in export")
    lo, hi = _window(params, (70, 130))
    ref_block = batch.of_type(SampleType.CAL_STD, SampleType.CAL_BLANK)
    refs = {}
    for istd in batch.istds:
        vals = [s.istd_intensities[istd.label] for s in ref_block
                if istd.label in s.istd_intensities]
        if vals:
            refs[istd.label] = sum(vals) / len(vals)
    if not refs:
        return _ne("istd_recovery", "no ISTD intensities found in calibration block")

    monitored = [s for s in batch.samples
                 if s.type not in {SampleType.CAL_STD, SampleType.CAL_BLANK}]
    details = []
    for label, ref in refs.items():
        recs, fail_rows = [], []
        for s in monitored:
            v = s.istd_intensities.get(label)
            if v is None:
                continue
            rec = v / ref * 100.0
            recs.append(rec)
            if not (lo <= rec <= hi):
                fail_rows.append({"istd": label, "sample": s.name,
                                  "recovery_pct": round(rec, 1),
                                  "window": f"{lo:g}-{hi:g}%", "ok": False})
        if not recs:
            details.append({"istd": label, "n": 0, "ok": None, "note": "no intensities"})
            continue
        details.append({"istd": label, "n": len(recs),
                        "min_pct": round(min(recs), 1), "max_pct": round(max(recs), 1),
                        "window": f"{lo:g}-{hi:g}%", "ok": not fail_rows})
        details.extend(fail_rows)
    return _finish("istd_recovery", details)


def lcs_recovery(batch: Batch, params: dict) -> CheckResult:
    """Lab control sample recovery."""
    lcs = batch.of_type(SampleType.LCS)
    if not lcs:
        return _ne("lcs_recovery", "no LCS in batch")
    lo, hi = _window(params, (80, 120))
    return _finish("lcs_recovery", _recovery_details(lcs, batch, lo, hi))


def dup_rpd(batch: Batch, params: dict) -> CheckResult:
    """Duplicate RPD, assessed only when both results exceed N x LOQ."""
    dups = batch.of_type(SampleType.DUP)
    if not dups:
        return _ne("dup_rpd", "no duplicate samples in batch")
    max_rpd = float(params.get("max_rpd_pct", 20))
    mult = float(params.get("min_conc_x_loq", 5))
    details = []
    for d in dups:
        parent = batch.find_sample(d.parent_name or "", SampleType.SAMPLE)
        if parent is None:
            details.append({"sample": d.name, "analyte": "-", "ok": None,
                            "note": f"parent '{d.parent_name}' not found"})
            continue
        for a in batch.analytes:
            va = (parent.results.get(a.label) or None) and parent.results[a.label].conc
            vb = (d.results.get(a.label) or None) and d.results[a.label].conc
            if va is None or vb is None:
                continue
            thr = mult * _loq_for(a.label, params)
            if min(va, vb) <= thr:
                details.append({"sample": d.name, "analyte": a.label, "rpd_pct": None,
                                "limit": max_rpd, "ok": None,
                                "note": f"below {mult:g}xLOQ, not assessed"})
                continue
            rpd = abs(va - vb) / ((va + vb) / 2.0) * 100.0
            details.append({"sample": d.name, "analyte": a.label,
                            "rpd_pct": round(rpd, 1), "limit": max_rpd,
                            "ok": rpd <= max_rpd})
    return _finish("dup_rpd", details,
                   warn_reason="duplicate present but every pair below assessment threshold")


def ms_msd(batch: Batch, params: dict) -> CheckResult:
    """Matrix spike / spike duplicate: recovery window + MS vs MSD RPD."""
    spikes = batch.of_type(SampleType.MS, SampleType.MSD)
    if not spikes:
        return _ne("ms_msd", "no MS/MSD samples in batch")
    lo, hi = _window(params, (75, 125))
    max_rpd = float(params.get("max_rpd_pct", 20))
    details = []
    for sp in spikes:
        parent = batch.find_sample(sp.parent_name or "", SampleType.SAMPLE)
        if parent is None or not sp.level:
            details.append({"sample": sp.name, "analyte": "-", "ok": None,
                            "note": "parent not found" if not parent else "no spike level"})
            continue
        for a in batch.analytes:
            rs, rp = sp.results.get(a.label), parent.results.get(a.label)
            if not rs or rs.conc is None or not rp or rp.conc is None:
                continue
            rec = (rs.conc - rp.conc) / sp.level * 100.0
            details.append({"sample": sp.name, "analyte": a.label,
                            "recovery_pct": round(rec, 1),
                            "window": f"{lo:g}-{hi:g}%", "ok": lo <= rec <= hi})
    ms_by = {s.parent_name: s for s in batch.of_type(SampleType.MS) if s.parent_name}
    msd_by = {s.parent_name: s for s in batch.of_type(SampleType.MSD) if s.parent_name}
    for pname in sorted(set(ms_by) & set(msd_by)):
        for a in batch.analytes:
            ra, rb = ms_by[pname].results.get(a.label), msd_by[pname].results.get(a.label)
            if not ra or ra.conc is None or not rb or rb.conc is None:
                continue
            rpd = abs(ra.conc - rb.conc) / ((ra.conc + rb.conc) / 2.0) * 100.0
            details.append({"sample": f"{pname} MS/MSD", "analyte": a.label,
                            "rpd_pct": round(rpd, 1), "limit": max_rpd,
                            "ok": rpd <= max_rpd})
    return _finish("ms_msd", details)


def serial_dilution(batch: Batch, params: dict) -> CheckResult:
    """Serial dilution agreement when the parent is sufficiently above LOQ."""
    dils = batch.of_type(SampleType.SERIAL_DIL)
    if not dils:
        return _ne("serial_dilution", "no serial dilution samples in batch")
    factor = float(params.get("factor", 5))
    agree = float(params.get("agreement_pct", 10))
    mult = float(params.get("min_conc_x_loq", 50))
    details = []
    for d in dils:
        parent = batch.find_sample(d.parent_name or "", SampleType.SAMPLE)
        if parent is None:
            details.append({"sample": d.name, "analyte": "-", "ok": None,
                            "note": f"parent '{d.parent_name}' not found"})
            continue
        for a in batch.analytes:
            rp, rd = parent.results.get(a.label), d.results.get(a.label)
            if not rp or rp.conc is None or not rd or rd.conc is None:
                continue
            if rp.conc <= mult * _loq_for(a.label, params):
                details.append({"sample": d.name, "analyte": a.label, "diff_pct": None,
                                "limit": agree, "ok": None,
                                "note": f"parent below {mult:g}xLOQ, not assessed"})
                continue
            expected = rp.conc / factor
            diff = abs(rd.conc - expected) / expected * 100.0
            details.append({"sample": d.name, "analyte": a.label,
                            "diff_pct": round(diff, 1), "limit": agree,
                            "ok": diff <= agree})
    return _finish("serial_dilution", details,
                   warn_reason="serial dilution present but parent below assessment threshold")


def seq_structure(batch: Batch, params: dict) -> CheckResult:
    """Required QC sample types are present in the batch."""
    required = [str(t) for t in params.get("require", [])]
    if not required:
        return _ne("seq_structure", "no required types configured in rule pack")
    present = {s.type.value for s in batch.samples}
    details = [{"required_type": t, "present": t in present, "ok": t in present}
               for t in required]
    return _finish("seq_structure", details)


#: catalog order == report order (SPEC §4)
CATALOG = {
    "cal_linearity": cal_linearity,
    "cal_low_std": cal_low_std,
    "icv_recovery": icv_recovery,
    "ccv_recovery": ccv_recovery,
    "ccv_frequency": ccv_frequency,
    "icb_ccb_blank": icb_ccb_blank,
    "method_blank": method_blank,
    "istd_recovery": istd_recovery,
    "lcs_recovery": lcs_recovery,
    "dup_rpd": dup_rpd,
    "ms_msd": ms_msd,
    "serial_dilution": serial_dilution,
    "seq_structure": seq_structure,
}
