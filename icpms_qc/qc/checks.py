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

import re
import statistics
from dataclasses import dataclass, field
from enum import Enum

from icpms_qc.model import ANALYSIS_TYPES, Batch, SampleType
from icpms_qc.qc import crm


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


def _recovery_row(sample_name: str, label: str, expected: float, result,
                  lo: float, hi: float) -> dict:
    """One recovery row, honest about censored results.

    A non-detect in a standard is not a missing number — it bounds recovery from
    above. When even that bound falls under the window the check can fail with
    certainty; when it does not, the row says so rather than inventing a value.
    """
    row = {"sample": sample_name, "analyte": label, "recovery_pct": None,
           "window": f"{lo:g}-{hi:g}%", "ok": None}
    if result is None or (result.conc is None and not result.below_dl):
        return {**row, "note": "no result"}
    if result.conc is None:                             # censored
        if result.dl is None:
            return {**row, "note": "non-detect, no limit quoted"}
        bound = result.dl / expected * 100.0
        if bound < lo:
            return {**row, "ok": False, "dl": result.dl,
                    "note": f"non-detect: recovery is at most {bound:.1f}%"}
        return {**row, "dl": result.dl,
                "note": f"non-detect at a limit allowing up to {bound:.1f}% — indeterminate"}
    rec = result.conc / expected * 100.0
    return {**row, "recovery_pct": round(rec, 1), "ok": lo <= rec <= hi}


def _recovery_details(samples, batch: Batch, lo: float, hi: float) -> list[dict]:
    details: list[dict] = []
    for s in samples:
        if not s.level:
            details.append({"sample": s.name, "analyte": "-", "recovery_pct": None,
                            "window": f"{lo:g}-{hi:g}%", "ok": None,
                            "note": "no expected level in export"})
            continue
        for a in batch.analytes:
            details.append(_recovery_row(s.name, a.label, s.level,
                                         s.results.get(a.label), lo, hi))
    return details


def _blank_details(samples, batch: Batch, params: dict) -> list[dict]:
    details: list[dict] = []
    for s in samples:
        for a in batch.analytes:
            r = s.results.get(a.label)
            thr = _blank_limit(a.label, params)
            base = {"sample": s.name, "analyte": a.label, "conc_ppb": None,
                    "limit_ppb": thr}
            if r is None or (r.conc is None and not r.below_dl):
                details.append({**base, "limit_ppb": None, "ok": None,
                                "note": "no result"})
                continue
            if r.conc is None:                          # censored non-detect
                if r.dl is not None and r.dl <= thr:
                    # Detection limit is itself under the threshold, so whatever
                    # the true value is, it clears — this blank passes on merit.
                    details.append({**base, "ok": True, "dl_ppb": r.dl,
                                    "note": "non-detect below the limit"})
                elif r.dl is not None:
                    details.append({**base, "ok": None, "dl_ppb": r.dl,
                                    "note": "non-detect, but at a limit above the "
                                            "threshold — cannot decide"})
                else:
                    details.append({**base, "ok": None,
                                    "note": "non-detect, no limit quoted"})
                continue
            details.append({**base, "conc_ppb": round(r.conc, 4),
                            "ok": abs(r.conc) < thr})
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


def cal_back_calc(batch: Batch, params: dict) -> CheckResult:
    """Every calibration standard back-calculates within its window.

    The correlation coefficient in `cal_linearity` is a summary statistic and a
    famously permissive one: an unweighted fit through a heteroscedastic ICP-MS
    curve routinely reports r > 0.999 while the bottom standard reads 50% low,
    because r is dominated by the top of the range. Eurachem and ICH Q2(R2) both
    make the same point — linearity is judged on residuals, not on r. This check
    is that judgement, applied level by level, with a wider window at the bottom
    where relative error legitimately grows.

    Overlaps `cal_low_std` on the lowest level by design: that check answers "is
    the reporting limit supported", this one answers "does the whole curve hold".
    """
    stds = [s for s in batch.of_type(SampleType.CAL_STD) if s.level]
    min_levels = int(params.get("min_levels", 3))
    if len(stds) < min_levels:
        return _ne("cal_back_calc",
                   f"need >={min_levels} cal standards with levels, found {len(stds)}")
    lo, hi = _window(params, (90, 110))
    low_lo, low_hi = params.get("low_window_pct", [70, 130])
    lowest = min(s.level for s in stds)
    details: list[dict] = []
    for s in sorted(stds, key=lambda s: s.level):
        is_low = s.level == lowest
        w_lo, w_hi = (float(low_lo), float(low_hi)) if is_low else (lo, hi)
        for a in batch.analytes:
            row = _recovery_row(s.name, a.label, s.level, s.results.get(a.label), w_lo, w_hi)
            details.append({**row, "level": s.level, "lowest_level": is_low})
    return _finish("cal_back_calc", details)


def cal_heteroscedasticity(batch: Batch, params: dict) -> CheckResult:
    """Relative calibration error that grows systematically toward the low end.

    The signature of an unweighted least-squares fit on data whose variance
    scales with concentration — the normal condition in ICP-MS, where counting
    statistics alone make the bottom of the curve noisier in relative terms. The
    remedy is a weighted fit (1/x or 1/x^2) in the instrument software; this
    check cannot apply one, only report that the residuals are asking for it.

    Diagnostic rather than acceptance criteria, so it warns by default. Set
    `on_exceed: fail` in the pack to make it binding.

    A ratio on its own is not evidence: when the top half happens to land almost
    exactly on nominal, a 1.5%-vs-0.1% split is a ratio of 15 and means nothing
    at all. `min_low_err_pct` is the floor that makes the ratio mean something —
    the low end has to be poor in absolute terms before being disproportionately
    poor is worth reporting.
    """
    stds = sorted([s for s in batch.of_type(SampleType.CAL_STD) if s.level],
                  key=lambda s: s.level)
    min_levels = int(params.get("min_levels", 4))
    if len(stds) < min_levels:
        return _ne("cal_heteroscedasticity",
                   f"need >={min_levels} cal standards with levels, found {len(stds)}")
    max_ratio = float(params.get("max_ratio", 3.0))
    min_low_err = float(params.get("min_low_err_pct", 10.0))
    as_fail = str(params.get("on_exceed", "warn")).lower() == "fail"
    half = len(stds) // 2
    low_half, high_half = stds[:half], stds[-half:]

    def _rel_errors(samples: list, label: str) -> list[float]:
        """|measured - nominal| / nominal, in %, over the levels that reported."""
        out = []
        for s in samples:
            r = s.results.get(label)
            if r is None or r.conc is None:      # censored or absent: no residual
                continue
            out.append(abs(r.conc - s.level) / s.level * 100.0)
        return out

    details: list[dict] = []
    flagged = 0
    for a in batch.analytes:
        low_err = _rel_errors(low_half, a.label)
        high_err = _rel_errors(high_half, a.label)
        base = {"analyte": a.label, "max_ratio": max_ratio,
                "min_low_err_pct": min_low_err,
                "n_low": len(low_err), "n_high": len(high_err)}
        if len(low_err) < 2 or len(high_err) < 2:
            details.append({**base, "ok": None,
                            "note": "need >=2 reported levels in each half"})
            continue
        low_med, high_med = statistics.median(low_err), statistics.median(high_err)
        row = {**base, "low_rel_err_pct": round(low_med, 2),
               "high_rel_err_pct": round(high_med, 2)}
        if high_med <= 0:
            # A perfect top half makes the ratio undefined, not infinite. Say so
            # rather than manufacturing a division that would flag every analyte.
            details.append({**row, "ratio": None, "ok": None,
                            "note": "top half fits exactly — ratio undefined"})
            continue
        ratio = low_med / high_med
        row["ratio"] = round(ratio, 2)
        if ratio > max_ratio and low_med >= min_low_err:
            flagged += 1
            details.append({**row, "ok": False if as_fail else None,
                            "note": (f"low-end relative error is {low_med:.1f}%, {ratio:.1f}x the "
                                     "high end — variance scales with concentration; an unweighted "
                                     "fit understates the bottom of the curve. Consider weighted "
                                     "least squares (1/x or 1/x^2).")})
        elif ratio > max_ratio:
            # Disproportionate but tiny: the top half simply landed on nominal.
            # Reporting this as a finding would cry wolf on every good curve.
            details.append({**row, "ok": True,
                            "note": (f"{ratio:.1f}x the high end, but only {low_med:.2f}% in "
                                     f"absolute terms (floor {min_low_err:g}%) — not actionable")})
        else:
            details.append({**row, "ok": True})

    if flagged and as_fail:
        return CheckResult("cal_heteroscedasticity", Outcome.FAIL,
                           reason=f"{flagged} analyte(s) exceed the low/high error ratio",
                           details=details)
    if flagged:
        return CheckResult("cal_heteroscedasticity", Outcome.WARN,
                           reason=(f"{flagged} analyte(s) show low-end relative error above "
                                   f"{max_ratio:g}x the high end — weighted fit indicated"),
                           details=details)
    return _finish("cal_heteroscedasticity", details)


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


def blank_derived_lod(batch: Batch, params: dict) -> CheckResult:
    """Detection/quantitation limits implied by this run's own blank scatter.

    The classic 3-sigma/10-sigma estimate, computed on the calibration blanks
    acquired *after* the calibration curve — the only blanks whose scatter
    reflects the instrument as it actually ran the samples. Compared against the
    LOQ the rule pack declares.

    This is the one check that audits the configuration rather than the batch: a
    reporting limit carried over from a better day makes every blank and every
    RPD threshold downstream of it optimistic, and nothing else would notice.
    """
    wanted, unknown = [], []
    for name in params.get("blank_types", ["ICB", "CCB"]):
        try:
            wanted.append(SampleType(str(name)))
        except ValueError:
            unknown.append(str(name))
    if not wanted:
        return _ne("blank_derived_lod",
                   f"no usable blank types configured (unrecognized: {', '.join(unknown)})")

    blanks = batch.of_type(*wanted)
    if params.get("after_calibration", True):
        cal = batch.of_type(SampleType.CAL_STD)
        if cal:
            last_cal = max(s.seq_index for s in cal)
            blanks = [b for b in blanks if b.seq_index > last_cal]

    min_n = int(params.get("min_n", 3))
    if len(blanks) < min_n:
        return _ne("blank_derived_lod",
                   f"need >={min_n} post-calibration blanks of type "
                   f"{'/'.join(t.value for t in wanted)}, found {len(blanks)}")

    k_lod, k_loq = float(params.get("k_lod", 3)), float(params.get("k_loq", 10))
    strict = str(params.get("on_exceed", "warn")).lower() == "fail"

    details, exceeded = [], 0
    for a in batch.analytes:
        vals = []
        for b in blanks:
            r = b.results.get(a.label)
            if r is not None and r.conc is not None:
                vals.append(r.conc)
        if len(vals) < min_n:
            # Blanks reported as "<DL" carry no number to take a spread of; say
            # that plainly instead of computing a standard deviation of nothing.
            details.append({"analyte": a.label, "n": len(vals), "ok": None,
                            "note": f"only {len(vals)} numeric blank result(s); "
                                    f"need {min_n} (non-detects carry no value)"})
            continue
        sd = statistics.stdev(vals)
        derived_loq = k_loq * sd
        configured = _loq_for(a.label, params)
        over = derived_loq > configured
        exceeded += over
        details.append({
            "analyte": a.label, "n": len(vals), "blank_sd": round(sd, 5),
            "derived_lod": round(k_lod * sd, 5), "derived_loq": round(derived_loq, 5),
            "configured_loq": configured,
            "ok": False if (over and strict) else (None if over else True),
            **({"note": "blank scatter implies a higher LOQ than the pack configures"}
               if over else {}),
        })

    if exceeded and not strict:
        return CheckResult(
            "blank_derived_lod", Outcome.WARN,
            reason=f"{exceeded} analyte(s) whose blank scatter implies an LOQ above "
                   f"the configured value",
            details=details)
    return _finish("blank_derived_lod", details,
                   warn_reason="no analyte had enough numeric blank results")


def precision_rsd(batch: Batch, params: dict) -> CheckResult:
    """Replicate precision (%RSD) of each measurement, per analyte.

    Every other check in the catalog asks whether a number is *right*. This one
    asks whether it is *repeatable*, which is a different failure and often the
    only one a research batch actually has — a run can recover its standards
    perfectly and still be unusable because the signal would not sit still.

    Precision is meaningless near background: counting statistics alone put the
    RSD of a blank in the hundreds of percent, and reporting that as a finding
    would bury the real ones. Measurements below `min_intensity_cps` (or, when
    intensities are not exported, below `min_conc_x_loq`) are recorded as not
    assessed rather than failed.

    One row per analyte, not per measurement: a wide export has thousands of
    measurements and nobody reads that, whereas "Zn was noisy all run" is the
    sentence an analyst acts on.
    """
    max_rsd = float(params.get("max_rsd_pct", 5.0))
    min_cps = params.get("min_intensity_cps")
    mult = float(params.get("min_conc_x_loq", 10))

    have_rsd = any(r.rsd_pct is not None
                   for s in batch.samples for r in s.results.values())
    if not have_rsd:
        return _ne("precision_rsd",
                   "no %RSD columns in this export — add `analyte_rsd_pattern` to "
                   "the template if the layout has them")

    types = params.get("types")
    wanted = None
    if types:
        wanted = {SampleType(str(t)) for t in types}

    details: list[dict] = []
    for a in batch.analytes:
        assessed, over, worst, worst_sample, skipped = 0, 0, None, None, 0
        for s in batch.samples:
            if wanted and s.type not in wanted:
                continue
            r = s.results.get(a.label)
            if r is None or r.rsd_pct is None:
                continue
            if min_cps is not None and r.intensity is not None:
                too_low = r.intensity < float(min_cps)
            elif r.conc is not None:
                too_low = r.conc <= mult * _loq_for(a.label, params)
            else:
                too_low = r.conc is None and r.intensity is None
            if too_low:
                skipped += 1
                continue
            assessed += 1
            if worst is None or r.rsd_pct > worst:
                worst, worst_sample = r.rsd_pct, s.name
            if r.rsd_pct > max_rsd:
                over += 1
        row = {"analyte": a.label, "n_assessed": assessed, "n_over": over,
               "max_rsd_pct": None if worst is None else round(worst, 2),
               "limit_pct": max_rsd, "ok": None if not assessed else over == 0}
        if worst_sample and over:
            row["worst_sample"] = worst_sample
        if not assessed:
            row["note"] = (f"all {skipped} measurement(s) below the assessment "
                           f"threshold — precision not evaluated")
        details.append(row)
    return _finish("precision_rsd", details,
                   warn_reason="no measurement was above the assessment threshold")


def instrument_flags(batch: Batch, params: dict) -> CheckResult:
    """What the instrument software itself objected to, carried into the report.

    The vendor already judged this run and wrote its verdict into the export. A
    tool that calls itself auditable and then silently drops that verdict is
    hiding evidence — and where the two disagree, the disagreement is precisely
    what a reviewer needs to see.

    These are the vendor's thresholds, not the rule pack's, so by default they
    are reported without deciding the batch. `on_flag: fail` makes them binding.
    """
    if not batch.flags_column_mapped:
        return _ne("instrument_flags",
                   "the template maps no instrument-flag column (`columns.flags`) — "
                   "the export may carry one")
    flagged = [s for s in batch.samples if s.instrument_flags or s.flags]
    if not flagged:
        return CheckResult("instrument_flags", Outcome.PASS,
                           details=[{"samples_flagged": 0, "ok": True,
                                     "note": "the instrument raised no QC objection "
                                             "on any sample in this batch"}])

    as_fail = str(params.get("on_flag", "warn")).lower() == "fail"
    total = sum(len(s.instrument_flags) for s in flagged)
    by_metric: dict[str, int] = {}
    for s in flagged:
        for f in s.instrument_flags:
            by_metric[f.metric] = by_metric.get(f.metric, 0) + 1

    details: list[dict] = [{
        "samples_flagged": len(flagged), "objections": total,
        "kinds": ", ".join(f"{k}×{n}" for k, n in sorted(by_metric.items())) or "-",
        "ok": False if as_fail else None,
        "note": "raised by the instrument software, not by this rule pack",
    }]
    limit = int(params.get("max_rows", 60))
    shown = 0
    for s in flagged:
        for f in s.instrument_flags:
            if shown >= limit:
                break
            details.append({"sample": s.name, "analyte": f.analyte,
                            "metric": f.metric, "value": f.value, "limit": f.limit,
                            "ok": False if as_fail else None})
            shown += 1
    if shown < total:
        details.append({"ok": None, "note": f"… and {total - shown} more objection(s); "
                                            f"see the export or raise `max_rows`"})
    if as_fail:
        return CheckResult("instrument_flags", Outcome.FAIL,
                           reason=f"{total} objection(s) from the instrument software",
                           details=details)
    return CheckResult("instrument_flags", Outcome.WARN,
                       reason=f"{total} objection(s) from the instrument software "
                              f"across {len(flagged)} sample(s)",
                       details=details)


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


def _fit_line(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Ordinary least squares y = slope·x + intercept, or None if degenerate."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def quant_crosscheck(batch: Batch, params: dict) -> CheckResult:
    """Recompute concentrations from raw counts and compare with the reported ones.

    Every other check takes the reported concentration as given. This one asks
    where that number came from: it builds a calibration out of the standards in
    the same export, predicts each sample from its own counts, and compares.

    The instrument software will never raise this, because it *is* the thing that
    produced the number — it applies the parameters it was given and does not
    doubt them. A dilution factor typed one digit wrong, an internal standard
    assigned to the wrong analyte, a stale calibration carried into a new batch:
    all of them produce confident, well-formatted, wrong results that every
    downstream QC check happily passes.

    A uniform offset is not a disagreement. If a dilution factor was applied that
    the export does not carry, *every* sample differs by exactly that factor —
    so a tight cluster of identical ratios is reported as a scale factor to
    explain, while scattered disagreement is reported as a finding. Without that
    distinction the check would cry wolf on every diluted batch.
    """
    cal = [s for s in batch.of_type(SampleType.CAL_STD) if s.level is not None]
    cal += [s for s in batch.of_type(SampleType.CAL_BLANK)]      # the zero point
    min_levels = int(params.get("min_levels", 3))
    if len(cal) < min_levels:
        return _ne("quant_crosscheck",
                   f"need >={min_levels} calibration points with levels, found {len(cal)}")

    have_cps = any(r.intensity is not None
                   for s in batch.samples for r in s.results.values())
    if not have_cps:
        return _ne("quant_crosscheck", "export carries no raw intensities to recompute from")

    # Internal-standard normalization only when the assignment is unambiguous.
    # Guessing which ISTD belongs to which analyte would silently change every
    # number this check produces.
    istd_label = batch.istds[0].label if len(batch.istds) == 1 else None
    if len(batch.istds) > 1:
        mode = "external calibration (multiple ISTDs, assignment not exported)"
    elif istd_label:
        mode = f"internal-standard normalized to {istd_label}"
    else:
        mode = "external calibration (no ISTD in export)"

    def response(sample, label) -> float | None:
        r = sample.results.get(label)
        if r is None or r.intensity is None:
            return None
        if istd_label:
            i = sample.istd_intensities.get(istd_label)
            return None if not i else r.intensity / i
        return r.intensity

    tol = float(params.get("max_deviation_pct", 15))
    mult = float(params.get("min_conc_x_loq", 10))
    uniform_cv = float(params.get("uniform_ratio_cv_pct", 5))
    min_cal_r = float(params.get("min_cal_r", 0.95))

    details: list[dict] = [{"calibration": mode, "cal_points": len(cal),
                            "tolerance_pct": tol, "ok": None}]
    for a in batch.analytes:
        pts = [(s.level or 0.0, y) for s in cal if (y := response(s, a.label)) is not None]
        if len(pts) < min_levels:
            continue
        # A curve whose response does not track the level is not a calibration —
        # most often the analyte simply is not in the standard that was run. Using
        # it anyway turns noise into confident nonsense, so it is skipped and the
        # instrument's own calibration flag is left to speak for it.
        cal_r = _pearson([p[0] for p in pts], [p[1] for p in pts])
        if cal_r < min_cal_r:
            details.append({"analyte": a.label, "cal_r": round(cal_r, 3), "ok": None,
                            "note": f"calibration response does not track level "
                                    f"(r={cal_r:.3f}) — not calibrated in this batch"})
            continue
        fit = _fit_line([p[0] for p in pts], [p[1] for p in pts])
        if fit is None or fit[0] == 0:
            details.append({"analyte": a.label, "ok": None,
                            "note": "calibration response is flat — cannot invert"})
            continue
        slope, intercept = fit

        # Compare only inside the range the calibration actually covers. Below the
        # lowest standard the inversion can return a negative concentration, and
        # above the highest it is extrapolation — neither says anything about
        # whether the reported number was computed correctly.
        levels = sorted({p[0] for p in pts if p[0] > 0})
        lo_cal = levels[0] * float(params.get("low_cal_fraction", 1.0))
        hi_cal = levels[-1]

        ratios, devs, worst, worst_s, skipped = [], [], None, None, 0
        for s in batch.samples:
            if s.type in {SampleType.CAL_STD, SampleType.CAL_BLANK}:
                continue
            r = s.results.get(a.label)
            y = response(s, a.label)
            if r is None or r.conc is None or y is None:
                continue
            if not (lo_cal <= r.conc <= hi_cal) or r.conc <= mult * _loq_for(a.label, params):
                skipped += 1
                continue
            predicted = (y - intercept) / slope
            if predicted <= 0:
                skipped += 1              # response sits below the calibration blank
                continue
            ratios.append(predicted / r.conc)
            dev = abs(predicted - r.conc) / r.conc * 100.0
            devs.append(dev)
            if worst is None or dev > worst:
                worst, worst_s = dev, s.name
        if len(devs) < 2:
            continue

        median_ratio = statistics.median(ratios)
        mean_ratio = statistics.fmean(ratios)
        cv = (statistics.stdev(ratios) / abs(mean_ratio) * 100.0) if mean_ratio else 999.0
        row = {"analyte": a.label, "n_compared": len(devs), "n_outside_range": skipped,
               "median_ratio": round(median_ratio, 4),
               "max_deviation_pct": round(worst, 1), "ok": True}

        if cv <= uniform_cv and abs(median_ratio - 1) * 100 > tol:
            # The one thing this comparison can prove. A constant offset survives
            # every unknown in the vendor's math — weighting, curve type, excluded
            # points, interference corrections all cancel in a ratio — so when
            # every sample is out by the same factor, something scaled the whole
            # column: a dilution factor, a unit, a transcription.
            details.append({**row, "ok": None, "scale_factor": round(median_ratio, 4),
                            "note": (f"every sample is out by the same factor "
                                     f"({median_ratio:.4g}x, spread {cv:.1f}%)")})
        elif worst > tol:
            # Scatter is not evidence. The export does not carry the regression
            # weighting, the curve type, which standards were excluded, the
            # interference-correction equations or the internal-standard
            # assignment, and any of them moves individual samples. Saying
            # "disagrees" here would be blaming the data for what the file omits.
            details.append({**row, "ok": None,
                            "note": (f"differs by up to {worst:.1f}% but not by a constant "
                                     f"factor (spread {cv:.1f}%) — expected: the export "
                                     f"omits the weighting, interference corrections and "
                                     f"ISTD assignment needed to reproduce the vendor's "
                                     f"arithmetic exactly")})
        else:
            details.append(row)

    # One analyte out by a constant is analyte-specific arithmetic — most often an
    # interference-correction equation, which the export does not carry. Checking
    # real batches, the masses that showed up this way were exactly the classically
    # corrected ones (ArO on 56 Fe, ClO on 51 V, ArC on 52 Cr, ArCl on 75 As), and
    # each carried its own different factor. A *dilution* cannot do that: it
    # multiplies the sample, so every analyte moves by the same number. That is the
    # discriminator, and only the second case is a finding.
    scaled = [d for d in details if d.get("scale_factor")]
    min_shared = int(params.get("min_shared_analytes", 3))
    if len(scaled) >= min_shared:
        factors = sorted(d["scale_factor"] for d in scaled)
        mid = factors[len(factors) // 2]
        agree = [d for d in scaled if abs(d["scale_factor"] - mid) / mid * 100 <= uniform_cv]
        if len(agree) >= min_shared and abs(mid - 1) * 100 > tol:
            for d in agree:
                d["ok"] = False
                d["note"] += " — shared across analytes, so it scales the sample, not one mass"
            return CheckResult(
                "quant_crosscheck", Outcome.FAIL,
                reason=(f"{len(agree)} analytes are out by the same factor ({mid:.4g}x) — "
                        f"a dilution, a unit or the wrong calibration applied to the batch"),
                details=details)

    for d in scaled:
        d["note"] += (" — one analyte only, so this is analyte-specific arithmetic the "
                      "export omits (an interference correction, typically), not a "
                      "batch-level scale")
    if scaled:
        return CheckResult(
            "quant_crosscheck", Outcome.WARN,
            reason=(f"{len(scaled)} analyte(s) sit at a constant offset from the recomputed "
                    f"value, each by a different factor — consistent with per-mass "
                    f"corrections rather than an error"),
            details=details)
    return _finish("quant_crosscheck", details,
                   warn_reason="no analyte had enough comparable samples")


def crm_recovery(batch: Batch, params: dict) -> CheckResult:
    """Reference material recovery, per reference value.

    Unlike every other recovery check, the expected values do not come from the
    export: a reference material carries dozens of elements at dozens of
    different values and the Level column can hold only one. They come from the
    certificate or compilation, kept as YAML in the CRM library (see
    configs/crm/README.md).

    Values marked `information` are reported but never decide the outcome. In a
    geochemical compilation that class can rest on a single lab's measurement,
    and failing someone's batch on one would be indefensible.
    """
    try:
        library = crm.load_library(str(params.get("library", "crm")))
    except (FileNotFoundError, ValueError, OSError) as exc:
        return _ne("crm_recovery", f"CRM library unavailable: {exc}")
    if not library:
        return _ne("crm_recovery",
                   "CRM library is empty — add a certificate to configs/crm/")

    hits = [(s, ref) for s in batch.samples
            if (ref := crm.match_sample(library, s.name)) is not None]
    if not hits:
        return _ne("crm_recovery",
                   f"no sample name matched any of the {len(library)} material(s) in "
                   f"the library ({', '.join(r.id for r in library)})")

    lo, hi = _window(params, (80, 120))
    details: list[dict] = []
    for s, ref in hits:
        for a in batch.analytes:
            cert = ref.certified.get(a.element) if a.element else None
            if cert is None:
                continue                        # not certified in this material
            row = {"sample": s.name, "crm": ref.id, "analyte": a.label,
                   "reference": cert.value, "unit": ref.unit,
                   "value_type": cert.value_type,
                   "recovery_pct": None, "window": f"{lo:g}-{hi:g}%", "ok": None}
            r = s.results.get(a.label)
            if r is None or (r.conc is None and not r.below_dl):
                details.append({**row, "note": "no result"})
                continue
            expected = crm.convert(cert.value, ref.unit, r.unit)
            if expected is None:
                details.append({**row, "note": f"cannot convert reference {ref.unit} "
                                               f"to reported {r.unit}"})
                continue
            if not expected:
                details.append({**row, "note": "reference value is zero"})
                continue

            out = _recovery_row(s.name, a.label, expected, r, lo, hi)
            merged = {**row, **out}
            if cert.uncertainty is not None and out.get("recovery_pct") is not None:
                u = crm.convert(cert.uncertainty, ref.unit, r.unit)
                if u is not None:
                    # Context, never the criterion: the source's uncertainty is
                    # far tighter than any method's recovery window.
                    merged["within_ref_uncert"] = abs(r.conc - expected) <= u
            if not cert.decisive:
                # Downgrade to informational without losing the number: the
                # recovery is still shown, it just cannot fail anyone's batch.
                merged["ok"] = None
                merged["note"] = (f"{cert.value_type} value — reported, not decisive"
                                  + (f"; {merged['note']}" if merged.get("note") else ""))
            details.append(merged)

        if ref.unfilled:
            # A half-transcribed file is normal; a silent one is not. Say which
            # elements the library still owes, so "PASS" is not read as "checked".
            details.append({"sample": s.name, "crm": ref.id, "analyte": "-",
                            "ok": None,
                            "note": f"{len(ref.unfilled)} element(s) in this material "
                                    f"have no value yet: {', '.join(ref.unfilled[:12])}"
                                    + (" …" if len(ref.unfilled) > 12 else "")})

    return _finish("crm_recovery", details,
                   warn_reason="a reference material was matched but none of its "
                               "values were measured in this batch")


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


def oes_line_agreement(batch: Batch, params: dict) -> CheckResult:
    """Do an element's emission lines agree with each other?

    Optical emission measures an element on several spectral lines at once, and
    they should give the same answer. This check has no mass-spectrometry
    counterpart, and it is the reason a lab looks at OES data by hand at all.

    What separates a real finding from noise is the *shape* of the disagreement.
    Scatter is imprecision. A line that reads consistently high across every
    sample is a spectral interference sitting on it, and that is the case worth
    naming: another element's emission falling on the same wavelength adds a
    contribution that does not vary randomly. Reporting a per-sample percentage
    would bury it.

    The tolerance and the way the difference is measured, relative to the larger
    of the pair, are taken from the reduction script this was checked against
    rather than invented here.
    """
    lines: dict[str, list] = {}
    for a in batch.analytes:
        if a.wavelength_nm is not None and a.element:
            lines.setdefault(a.element, []).append(a)
    multi = {e: v for e, v in lines.items() if len(v) >= 2}
    if not multi:
        return _ne("oes_line_agreement",
                   "no element is measured on more than one emission line")

    tol = float(params.get("max_relative_diff", 0.25))
    mult = float(params.get("min_conc_x_loq", 10))
    bias_share = float(params.get("systematic_share", 0.8))

    details: list[dict] = []
    for element, group in sorted(multi.items()):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                diffs, higher = [], []
                for s in batch.samples:
                    ra, rb = s.results.get(a.label), s.results.get(b.label)
                    if not ra or not rb or ra.conc is None or rb.conc is None:
                        continue
                    biggest = max(abs(ra.conc), abs(rb.conc))
                    if biggest <= mult * _loq_for(a.label, params):
                        continue          # near the blank, the ratio means nothing
                    diffs.append(abs(ra.conc - rb.conc) / biggest)
                    higher.append(ra.conc > rb.conc)
                if len(diffs) < 2:
                    continue

                worst = max(diffs)
                row = {"element": element, "lines": f"{a.label} vs {b.label}",
                       "n": len(diffs), "max_diff_pct": round(worst * 100, 1),
                       "tolerance_pct": round(tol * 100, 1), "ok": worst <= tol}
                if worst > tol:
                    share = max(sum(higher), len(higher) - sum(higher)) / len(higher)
                    if share >= bias_share:
                        high = a.label if sum(higher) >= len(higher) / 2 else b.label
                        row["note"] = (
                            f"{high} reads higher in {share:.0%} of samples, not at "
                            f"random — the signature of a spectral interference on "
                            f"that line rather than imprecision")
                    else:
                        row["note"] = ("the two lines disagree without either being "
                                       "consistently higher, which reads as scatter "
                                       "rather than an interference")
                details.append(row)

    return _finish("oes_line_agreement", details,
                   warn_reason="no pair of lines had enough comparable samples")


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
    "cal_back_calc": cal_back_calc,
    "cal_heteroscedasticity": cal_heteroscedasticity,
    "cal_low_std": cal_low_std,
    "icv_recovery": icv_recovery,
    "ccv_recovery": ccv_recovery,
    "ccv_frequency": ccv_frequency,
    "icb_ccb_blank": icb_ccb_blank,
    "method_blank": method_blank,
    "blank_derived_lod": blank_derived_lod,
    "precision_rsd": precision_rsd,
    "instrument_flags": instrument_flags,
    "istd_recovery": istd_recovery,
    "lcs_recovery": lcs_recovery,
    "quant_crosscheck": quant_crosscheck,
    "crm_recovery": crm_recovery,
    "dup_rpd": dup_rpd,
    "ms_msd": ms_msd,
    "serial_dilution": serial_dilution,
    "oes_line_agreement": oes_line_agreement,
    "seq_structure": seq_structure,
}
