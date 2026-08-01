# icpqc — Specification v0.1 (draft, 2026-07-19)

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
                       configs/ rule pack (YAML) ──► qc/ engine ──► CheckResult[]
                                                      │
                                             report/ (HTML + JSON)
                                                      │
                                                  cli (icpqc check)
```

- `io/` — one parser per *layout family*, selected by a **template** (YAML): column-pattern → field mapping, sample-type vocabulary mapping, analyte-header regex. Rationale: MassHunter export layouts vary by version and report template; mappings are data, not code.
- `qc/` — small, pure check functions over the canonical model; a rule pack parameterizes thresholds and which checks are active. Checks never read CSV directly.
- `report/` — renders `CheckResult[]` + batch metadata. JSON schema is a stable public contract (semver'd).
- `cli` — `icpqc check <export.csv> --rules <pack> --template <tpl> [--out DIR]`; exit code 0 = all pass, 2 = QC failures (CI/automation-friendly).

## 3. Canonical batch model

```
Batch:      instrument_family, exported_at?, analytes[], istds[], samples[]
Analyte:    mass, element, label            (e.g. 75 As [He])
Sample:     name, seq_index, type, level?, results{analyte_label → Result}
Result:     conc, unit, intensity?, istd_label?, istd_intensity?, flags[]
SampleType: CAL_STD | CAL_BLANK | ICV | CCV | ICB | CCB | MB | LCS |
            SAMPLE | DUP | MS | MSD | SERIAL_DIL | POST_SPIKE | OTHER
```

Sample-type detection = template vocabulary (exact strings labs use, e.g. `"CCV"`,
`"QC3"`) + optional name-pattern rules. Anything unrecognized → `OTHER` + warning (never
silently guessed).

## 4. QC check catalog (v0.1)

Every check: `id`, inputs, pass condition, method reference. **All numeric defaults below
are typical values for EPA 6020B-style / 200.8-style work and are marked `verify:` in rule
packs — confirm against the current method text before compliance use.**

| id | What it checks | Typical default |
|---|---|---|
| `cal_linearity` | calibration correlation coefficient per analyte | r ≥ 0.998 |
| `cal_low_std` | lowest non-zero standard recovers | ±30% |
| `icv_recovery` | initial cal verification (2nd source) | 90–110% |
| `ccv_recovery` | continuing cal verification | 90–110% |
| `ccv_frequency` | CCV cadence in sequence | every ≤10 analyses + end of run |
| `icb_ccb_blank` | cal blanks below reporting threshold | |conc| < LOQ (configurable: MDL / ½LOQ) |
| `method_blank` | MB below reporting threshold | < LOQ |
| `istd_recovery` | ISTD intensity vs reference (ICAL std/blank) | 200.8-pack: 60–125% · 6020B-pack: 70–130% (**verify**) |
| `lcs_recovery` | lab control sample | 80–120% |
| `dup_rpd` | duplicate relative percent difference | ≤ 20% (when both > 5×LOQ) |
| `ms_msd` | matrix spike / spike dup recovery + RPD | 75–125%, RPD ≤ 20% |
| `serial_dilution` | 5× dilution agreement (conc sufficiently above LOQ) | within ±10% |
| `seq_structure` | required QC types present (ICV/ICB after cal, MB, LCS per batch) | pack-defined |

Per-check outcome: `PASS / FAIL / WARN / NOT_EVALUATED(reason)` — a check that can't run
(missing QC sample) must say so loudly; silence is how QC reports lie.

## 5. Rule packs

`configs/epa6020b.yaml` (ships as example), `configs/epa200_8.yaml`, `configs/custom.example.yaml`.
Each entry: `check id → {enabled, params, verify: <method section to confirm>}`. Labs
version their pack in git → the QC policy itself becomes reviewable and auditable.

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
