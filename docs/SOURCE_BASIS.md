# Source basis — what grounds each check, and what does not

Every check in the catalog asserts something. This document records **where that
assertion comes from**, so a reviewer can argue with the grounding rather than
guess at it. It is the companion to the `verify:` note carried by each entry in
a rule pack.

## Read this first

**icpqc ships typical values, not method text.** The thresholds in
`configs/*.yaml` are defaults chosen to be recognisable, not transcriptions of
any standard. Before any compliance use, verify every threshold against the
current revision of the method your laboratory actually runs under, and record
that you did.

**Clause numbers are deliberately absent.** Section numbering moves between
revisions, and a citation that looks precise but points at the wrong revision is
worse than no citation. This document names frameworks and the requirement in
plain words; your SOP supplies the clause.

## Frameworks referenced

| Short name | What it is |
|---|---|
| **EPA SW-846 6020B** | US EPA method for ICP-MS, solid and aqueous waste matrices |
| **EPA 200.8** | US EPA method for ICP-MS, drinking and ambient waters |
| **40 CFR 136 App. B** | US procedure for determining the method detection limit |
| **ISO 17294-2** | International standard for water analysis by ICP-MS |
| **Eurachem Guide** | *The Fitness for Purpose of Analytical Methods* — method validation guidance |
| **ICH Q2(R2)** | Validation of analytical procedures (pharma, but the statistics are general) |
| **IUPAC** | Recommendations on detection/quantitation capability terminology |
| **ISO 11843** | Capability of detection |
| **AOAC** | Appendix F guidance on single-laboratory validation |

## Check-by-check

| Check | What it asserts | Grounded in | Caveat |
|---|---|---|---|
| `cal_linearity` | Correlation coefficient of the calibration meets a floor | 6020B / 200.8 ICAL acceptance; lab SOP | **r is a weak criterion — see below.** Kept because instrument software reports it and labs are used to it |
| `cal_back_calc` | Every calibration standard back-calculates within a window, widened at the bottom | Eurachem; ICH Q2(R2); 6020B/200.8 ICAL verification practice | Window values are conventional, not quoted from method text |
| `cal_heteroscedasticity` | Low-end relative error is not disproportionately worse than the high end | Eurachem; ICH Q2(R2) — weighted regression where variance scales with concentration | **Diagnostic, not an acceptance criterion.** No EPA method requires it. Warns by default |
| `cal_low_std` | The lowest standard recovers within window | 6020B / 200.8 low-level standard practice | Supports the reporting limit; overlaps `cal_back_calc` by design |
| `icv_recovery` | Second-source verification recovers within window | 6020B ICV / 200.8 QCS | — |
| `ccv_recovery` | Continuing verification recovers within window | 6020B CCV / 200.8 IPC | — |
| `ccv_frequency` | Verification runs often enough and closes the sequence | 6020B / 200.8 QC frequency | `every_n` is a pack policy |
| `icb_ccb_blank` | Calibration blanks sit below the reporting threshold | 6020B / 200.8 calibration blank acceptance | Threshold expressed relative to the pack's LOQ |
| `method_blank` | Preparation blank sits below the reporting threshold | 6020B / 200.8 reagent blank acceptance | — |
| `blank_derived_lod` | The configured LOQ is defensible against this run's own blank scatter | 40 CFR 136 App. B; IUPAC; ISO 11843 | **3σ/10σ on a handful of blanks is not an MDL study** — 40 CFR wants 7+ replicates. Audits configuration, not the batch |
| `istd_recovery` | Internal standard response holds against the calibration block | 6020B / 200.8 internal standards | **The two methods use different windows** — confirm which applies |
| `lcs_recovery` | Laboratory control sample recovers within window | 6020B LCS / 200.8 LFB | Often superseded by in-house control limits |
| `crm_recovery` | Certified material recovers against its certificate | Certificate of analysis; lab control limits | Values come from `configs/crm/*.yaml` — the certificate is the authority |
| `dup_rpd` | Duplicate agreement within RPD, above a concentration floor | 6020B / 200.8 duplicate criteria | **See the replicate-hierarchy caveat below** |
| `ms_msd` | Matrix spike recovery and RPD within window | 6020B / 200.8 matrix spike criteria | — |
| `serial_dilution` | Diluted result agrees, above a concentration floor | 6020B serial dilution test | Not a standard 200.8 element |
| `seq_structure` | The sequence contains the QC types the pack requires | Lab SOP / method QC section | Pure policy — no method mandates a single layout |

## Why r is not enough (and what replaced it)

An ICP-MS calibration is heteroscedastic by construction: counting statistics
alone make the bottom of the curve noisier in relative terms than the top. The
correlation coefficient is computed across the whole range and is dominated by
the high standards, so a curve whose lowest standard back-calculates to **half**
its nominal value can still report r > 0.999.

Eurachem and ICH Q2(R2) both make the same point in their own words: linearity
is judged on **residuals**, not on a correlation coefficient. That is what
`cal_back_calc` does — it holds every level to a window instead of summarising
the curve into one number.

`cal_heteroscedasticity` goes one step further and reports the *pattern*: when
low-end relative error is both large in absolute terms and disproportionately
larger than the high end, the residuals are asking for a weighted fit (1/x or
1/x²). icpqc cannot refit the curve — that lives in the instrument software —
so this check only says so, and warns rather than fails.

⚠️ **The absolute floor matters.** A ratio on its own is not evidence: when the
top half happens to land almost exactly on nominal, a 1.5%-vs-0.1% split is a
ratio of 15 and means nothing. `min_low_err_pct` is the floor that keeps the
check from crying wolf on good curves. This was found by the check firing on the
project's own clean reference batch; the regression test is
`tests/test_cal_curve.py::test_a_ratio_alone_is_not_a_finding`.

## Known gap: replicate hierarchy

`dup_rpd` currently treats a duplicate as a duplicate. It does **not** model
*which layer* the replicate belongs to — an independent re-preparation, a
re-digest, a repeat injection of the same solution, or co-added transients of
the same acquisition are statistically very different things, and treating a
repeat injection as an independent replicate flatters precision.

The export usually does not say which layer was performed, so inferring it would
be guessing. Recording the gap here is the honest position until the model can
carry the distinction. Analytical validation guidance (Eurachem, ICH Q2(R2))
separates repeatability, intermediate precision, and reproducibility for exactly
this reason.
