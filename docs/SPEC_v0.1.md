# icpms-qc — Specification v0.1 (draft, 2026-07-19)

**Provenance note (clean-room):** this spec is drafted solely from public sources — the
publicly available texts of EPA SW-846 Method 6020B and EPA Method 200.8, Agilent's public
community documentation about export capabilities, and general software practice. No
employer-owned code and no client data were consulted.

## 1. Goals / non-goals

**v0.1 goals**
1. Parse an Agilent MassHunter quantitative batch export (CSV) into a canonical batch model via a template mapping
2. Run a configurable set of QC checks (rule pack) over the batch
3. Emit an audit-ready HTML report + a stable JSON sidecar (for automation/AI agents)
4. Ship with a synthetic-data generator so demos and tests never need real data

**Non-goals (v0.1)**
- Instrument control, acquisition, raw-signal processing (counts→conc math stays in vendor software)
- Vendor binary formats (exports only — legally clean, version-stable)
- LIMS integration, cloud anything, GUIs
- Statistical process control beyond the defined checks (v0.2+)

## 2. Architecture

```
export CSV ──► io/ (template-driven parser) ──► canonical BatchModel
                                                      │
                       icpms_qc/configs/ rule pack (YAML) ──► qc/ engine ──► CheckResult[]
                                                      │
                                             report/ (HTML + JSON)
                                                      │
                                                  cli (icpms-qc check)
```

- `io/` — one parser per *layout family*, selected by a **template** (YAML): column-pattern → field mapping, sample-type vocabulary mapping, analyte-header regex. Rationale: MassHunter export layouts vary by version and report template; mappings are data, not code.
- `qc/` — small, pure check functions over the canonical model; a rule pack parameterizes thresholds and which checks are active. Checks never read CSV directly.
- `report/` — renders `CheckResult[]` + batch metadata. JSON schema is a stable public contract (semver'd).
- `cli` — `icpms-qc check <export.csv> --rules <pack> --template <tpl> [--out DIR]`; exit code 0 = all pass, 2 = QC failures (CI/automation-friendly).

## 3. Canonical batch model

```
Batch:      instrument_family, exported_at?, analytes[], istds[], samples[]
Analyte:    label, mass, element, mass_shift?, mode?    (e.g. 31 -> 47 P [O2])
Sample:     name, seq_index, type, level?, results{analyte_label → Result}
Result:     conc, unit, intensity?, istd_label?, istd_intensity?, flags[],
            below_dl, dl?, rsd_pct?
Sample:     … flags[], instrument_flags[InstrumentFlag]
SampleType: CAL_STD | CAL_BLANK | ICV | CCV | ICB | CCB | MB | LCS |
            SAMPLE | DUP | MS | MSD | SERIAL_DIL | POST_SPIKE | OTHER
```

Sample-type detection = template vocabulary (exact strings labs use, e.g. `"CCV"`,
`"QC3"`) + optional name-pattern rules. Anything unrecognized → `OTHER` + warning (never
silently guessed).

**Analyte identity.** `label` is the export's own text and is never rewritten — it is the
key every `Sample.results` dict uses. `mass`/`element`/`mode` are parsed from it, and
`mass_shift` is set only for triple-quad MS/MS acquisitions (`78 -> 78 Se [He]` on an
8800/8900). An element symbol that is not a real symbol, or that is a truncated word
(`220 Bkg`), yields `element = None` rather than a plausible invention: checks that join
on element must skip a column they cannot identify, not mis-join it.

**Censored results.** An export writes a below-detection result as `<0.05` or `ND`, not as
a number. `conc = None, below_dl = True, dl = 0.05` keeps that distinct from `conc = None,
below_dl = False`, which means nothing was reported at all. The distinction is load-bearing:
a blank censored below its threshold *passes*, a blank nobody measured is `NOT_EVALUATED`,
and collapsing the two makes those two batches produce the same report.

**Precision.** `rsd_pct` is the export's own repeatability figure for a measurement.
Accuracy checks cannot see it, and a run can recover every standard perfectly while
remaining unusable because the signal would not sit still — so the templates parse it
rather than discarding it, and `precision_rsd` acts on it.

**The instrument's own verdict.** MassHunter writes its QC objections into the export as
free text. They are parsed into `Sample.instrument_flags` — the blob concatenates
sentences with no delimiter (`= 5.00` runs straight into the next analyte's mass `66`), so
it is cut at the *known analyte labels* from the header rather than guessed at by regex.
`Batch.flags_column_mapped` separates "the instrument raised no objection" from "no column
carrying its objections was mapped"; both are an empty list and they must never report the
same way.

## 4. QC check catalog (v0.1)

Every check: `id`, inputs, pass condition, method reference. **All numeric defaults below
are typical values for EPA 6020B-style / 200.8-style work and are marked `verify:` in rule
packs — confirm against the current method text before compliance use.**

| id | What it checks | Typical default |
|---|---|---|
| `cal_linearity` | calibration correlation coefficient per analyte | r ≥ 0.998 |
| `cal_back_calc` | each standard back-calculates to its nominal value | 90–110%, low level 70–130% |
| `cal_heteroscedasticity` | low-end relative error vs high end (weighted-fit indicator) | ratio ≤ 3, floor on low-end error; WARN |
| `cal_low_std` | lowest non-zero standard recovers | ±30% |
| `icv_recovery` | initial cal verification (2nd source) | 90–110% |
| `ccv_recovery` | continuing cal verification | 90–110% |
| `ccv_frequency` | CCV cadence in sequence | every ≤10 analyses + end of run |
| `icb_ccb_blank` | cal blanks below reporting threshold | |conc| < LOQ (configurable: MDL / ½LOQ) |
| `method_blank` | MB below reporting threshold | < LOQ |
| `blank_derived_lod` | LOD/LOQ implied by post-cal blank scatter, vs the configured LOQ | 3σ / 10σ, ≥3 blanks, warn on exceed |
| `precision_rsd` | replicate %RSD per analyte, gated on signal level | ≤ 5% (pack), not assessed below the gate |
| `instrument_flags` | the instrument software's own QC objections, carried into the report | reported; `on_flag: fail` to bind |
| `istd_recovery` | ISTD intensity vs reference (ICAL std/blank) | 200.8-pack: 60–125% · 6020B-pack: 70–130% (**verify**) |
| `lcs_recovery` | lab control sample | 80–120% |
| `quant_crosscheck` | recomputes concentration from raw counts and compares with the reported value | ≤15% deviation; a uniform ratio is reported as a scale factor |
| `crm_recovery` | certified reference material, per certified element | 80–120% vs `icpms_qc/configs/crm/*.yaml` |
| `dup_rpd` | duplicate relative percent difference | ≤ 20% (when both > 5×LOQ) |
| `ms_msd` | matrix spike / spike dup recovery + RPD | 75–125%, RPD ≤ 20% |
| `serial_dilution` | 5× dilution agreement (conc sufficiently above LOQ) | within ±10% |
| `laser_log_alignment` | LA-ICP-MS: laser log vs reduced results — same run? | counts + names agree; granularity `auto` |
| `seq_structure` | required QC types present (ICV/ICB after cal, MB, LCS per batch) | pack-defined |

Per-check outcome: `PASS / FAIL / WARN / NOT_EVALUATED(reason)` — a check that can't run
(missing QC sample) must say so loudly; silence is how QC reports lie.

## 5. Rule packs

`icpms_qc/configs/epa6020b.yaml` (ships as example), `icpms_qc/configs/epa200_8.yaml`, `icpms_qc/configs/custom.example.yaml`.
Each entry: `check id → {enabled, params, verify: <method section to confirm>}`. Labs
version their pack in git → the QC policy itself becomes reviewable and auditable.

Two checks are worth calling out as different in kind:

- `blank_derived_lod` audits the **rule pack** rather than the batch. Every blank threshold
  and RPD cutoff in the pack is expressed relative to `loq_ppb`; if that value was set on a
  better day, everything downstream of it is quietly optimistic and no other check notices.
- `crm_recovery` is the only check whose expected values do not come from the export, for
  the reason in §5.1.

### 4.1 Second input: the laser log (LA-ICP-MS)

`icpms-qc check results.csv --laser-log LaserLog.csv`

The laser and the mass spectrometer are two instruments with two clocks, started by
two computers. The laser log knows *when it fired and where*; the ICP data knows
*what it counted*. Nothing in either file states which counts belong to which
ablation — that correspondence is reconstructed downstream, and when it is
reconstructed wrongly every number after it is wrong while the report stays green.

**icpms-qc does not perform the alignment.** Segmenting a transient signal is
reduction (SPEC §1 non-goals) and the tools that do it — pewpew/pewlib, Ilaps,
iolite, laserTRAM — do it well. What none of them does is *audit* the outcome.
`icpms_qc.io.laserlog` parses the laser's own record into `Batch.laser_log` so
`laser_log_alignment` can compare it against the reduced results.

**Granularity is the crux and must not be assumed.** One log *sequence* is one
pattern, carrying the sample name; inside it are the individual *ablations* (lines
or spots). Whether a reduced row corresponds to a sequence or to an ablation is a
property of the workflow, not of the file. `granularity: auto` resolves it by
whichever count matches — and when neither matches, that is reported as the
finding, with both numbers, rather than settled by picking the closer one.

What the check can establish without touching a single count:

- **count agreement** — patterns fired vs rows reported; a lost trigger or a
  dropped sequence shows up here and nowhere else
- **position-by-position name agreement** — the cheapest off-by-one detector
  there is, and the one that catches a bracketing standard landing on the wrong row
- **ablation duration consistency** — a shot much shorter than its neighbours is
  an aborted ablation whose result rests on less signal than everything around it

The log also carries timestamped `MFC1`/`MFC2`/`Cell Pressure`, parsed into
`LaserLog.environment` — carrier-gas stability for free, from a file already read.

### 5.1 CRM library (`icpms_qc/configs/crm/*.yaml`)

Every other recovery check divides by `Sample.level` — one expected concentration for all
analytes, which is the right shape for a spike from a single multi-element standard and the
wrong shape for a reference material. A CRM certifies dozens of elements at dozens of
different values, each with its own uncertainty, and the export carries none of them.

One YAML file per material: `unit`, `match.name_patterns` (samples are matched by name,
since labs type a CRM as LCS, QC or Sample interchangeably), and `certified: {element →
{value, uncertainty?}}`. Units are converted at check time; an unrecognized unit produces a
NOT-ASSESSED row rather than a number, because a certificate in mg/L read against results
in ppb is wrong by 1000×. The certificate's uncertainty is reported as context and never
used as the criterion — that window is a QC policy decision and lives in the rule pack.

## 6. Report

- **HTML**: batch header, sequence table with per-sample flag chips, one section per check
  with the numbers behind every verdict, final batch verdict. Print-clean (PDF via browser).
- **JSON sidecar**: full CheckResult detail, schema-versioned. This is the contract lab
  automation and AI agents consume — treat it as API.

## 7. Testing

- Golden tests: synthetic batches (from `tools/gen_synthetic_data.py`, seeded) with known
  injected violations → expected verdicts. One golden pair per check.
- Template tests: each contributed real-world (redacted) export gets a fixture + parse test.

## 8. Milestones

1. **M1**: parser + canonical model + `cal_linearity`/`ccv_recovery`/blank checks + JSON out
2. **M2**: full v0.1 catalog + HTML report → *publishable v0.1, announce*
3. **M3** (v0.2): SQLite history + trend charts
4. **M4** (v0.3): Qtegra template family

## 9. Release checklist

- [ ] All rule-pack thresholds verified against the current text of each method
- [ ] README carries the non-affiliation + trademark note
