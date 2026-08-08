# Using icpms-qc

A walkthrough from "I have a CSV" to "I have a report someone else can read".

Everything below has been run as written. Exit codes: **0** = all checks passed,
**2** = QC failures present, **1** = error.

---

## 1. Install

```bash
git clone <repo> && cd icpms-qc
pip install .
```

Python ≥ 3.10. The only runtime dependency is `pyyaml`. For the test suite:
`pip install -e ".[dev]" && pytest`.

Installing puts an `icpms-qc` command on your PATH. If you'd rather not install,
every example below also works as `python -m icpms_qc.cli …` from the repo root.

---

## 2. Five minutes, no real data

The repo ships a generator so you can see a full report before trusting it with
anything of yours. Nothing here touches an instrument.

```bash
python tools/gen_synthetic_data.py demo_batch.csv
icpms-qc check demo_batch.csv --rules epa6020b
```

```
icpms-qc PASS: demo_batch.csv
  [         PASS] cal_linearity
  [         PASS] cal_back_calc
  ...
  [NOT_EVALUATED] serial_dilution - no serial dilution samples in batch
  report: out/qc_report.html
  json:   out/qc_report.json
```

Open `out/qc_report.html`. Then see a failing run:

```bash
python tools/gen_synthetic_data.py bad_batch.csv --violations
icpms-qc check bad_batch.csv --rules epa6020b --out out_fail
```

Three failures are injected on purpose (a CCV at ~85%, internal-standard drift to
~65%, a duplicate at ~30% RPD), so you can see what a real finding looks like and
confirm the tool actually catches things.

---

## 3. Your own export

### 3a. Try a shipped template first

```bash
icpms-qc check my_batch.csv --rules epa6020b --template masshunter_quant_wide
```

If the layout matches, you're done. If it doesn't, you'll get a loud error rather
than a wrong answer:

```
icpms-qc: error: no analyte columns matched template 'masshunter_quant_wide' —
wrong template for this export?
```

Shipped templates:

| template | layout |
|---|---|
| `masshunter_quant_wide` | one header row, `9 Be Conc. [ppb]` style columns |
| `masshunter_conc_2row` | two header rows (analyte labels above `Conc.`/`RSD` sub-headers), cp1252, Level column holds an index |
| `masshunter_counts_2row` | as above, raw counts rather than concentrations |

### 3b. Look at what you actually have

```bash
icpms-qc inspect my_batch.csv
```

Prints a **layout fingerprint** — column headers, detected encoding, per-column
kind, and categorical vocabularies. Measurement values are never included, and
free text outside lab vocabulary is masked, so you can paste this somewhere or
send it to someone without leaking sample identities. Add `--include-names` if
you want sample names verbatim (it will tell you it did).

This is also the fastest way to diagnose a mismatch: compare the column names it
found against the pattern in the template you tried.

### 3c. Let a model draft the template

```bash
icpms-qc template-from-header my_batch.csv --id my_lab
```

It sends the *fingerprint* (never measurements), asks a model for a template,
then **validates the draft by parsing your actual file** and reports what it
understood — how many samples, which analytes, which sample types fell through:

```
  samples parsed : 47
  analytes       : 12  (9 Be, 52 Cr [He], …)
  internal stds  : 3   (45 Sc, 115 In, 209 Bi)
  sample types   : CAL_STD×5, CCV×4, SAMPLE×36
  !! 2 row(s) fell through to OTHER — checks that need them will report NOT_EVALUATED
  → review the flagged items above before accepting
```

**Nothing is written until you pass `--accept`.** Read the draft first: a
template is a claim about your data.

```bash
icpms-qc template-from-header my_batch.csv --id my_lab --accept
icpms-qc check my_batch.csv --template my_lab --rules epa6020b
```

Once accepted the template is a plain YAML file and every future run is
deterministic — no model involved, same export gives the same report in three
years. Add `--resolve` if sample types stayed unmapped; it shows you exactly
which sample names it would disclose before sending anything.

Needs the [`claude` CLI](https://claude.com/claude-code) or `ANTHROPIC_API_KEY`.
Without either, `icpms-qc inspect` still works — hand the fingerprint to any model,
or write the template by hand.

---

## 4. Reading the report

Every check reports one of four outcomes:

| | meaning |
|---|---|
| **PASS** | evaluated, within limits |
| **FAIL** | evaluated, outside limits — the detail table names the rows |
| **WARN** | evaluated, but nothing decisive, or a diagnostic worth your attention |
| **NOT_EVALUATED** | **could not run, and says why** — a missing CCV is not a pass |

That last one matters most. A check that quietly skips is how QC reports lie, so
icpms-qc always states what it could not assess. `serial_dilution - no serial
dilution samples in batch` means exactly that: nobody checked, and you now know.

The HTML report carries, in order: parser warnings, the analytes as icpms-qc
understood them (a blank element column means a header it could not read — every
element-keyed check has been skipping it), the sequence, then one section per
check with the numbers behind the verdict.

`out/qc_report.json` is the same content as a stable, versioned contract — that's
what automation and agents should read, not the HTML.

---

## 5. Making the rules yours

The shipped packs are **typical defaults, not method text**. Copy one and edit:

```bash
cp configs/epa6020b.yaml configs/mylab.yaml
icpms-qc check my_batch.csv --rules configs/mylab.yaml
```

A pack entry looks like:

```yaml
shared:
  loq_ppb:
    default: 0.1          # your reporting limits, per analyte label or default
checks:
  ccv_recovery:
    enabled: true
    params: { window_pct: [90, 110] }
    verify: "6020B continuing calibration verification"
```

- `enabled: false` switches a check off entirely
- `params` holds every threshold — there are no numeric limits hidden in the code
- `verify:` is a note to yourself about which method section to confirm against;
  it is printed in the report

Keep your pack in git. That's the point: **your QC policy becomes a reviewable,
diffable file** rather than a setting someone changed once.

`configs/facility_basic.yaml` is a good starting point for a research facility —
it evaluates the calibration and QC re-reads that such runs actually contain, and
leaves the EPA batch-QC checks (method blank, LCS, spikes) switched off rather
than failing you for not having them.

---

## 6. Reference materials

A CRM certifies dozens of elements at dozens of different values, which does not
fit the single Level column an export carries. Put the certificate in
`configs/crm/`:

```yaml
id: nist_srm_1640a
unit: ppb
provenance: { compilation: "NIST certificate", version: "…", accessed: "2026-08-07" }
match:
  name_patterns: ['(?i)\bSRM\s*1640a\b']     # how your lab names it in the sequence
certified:
  As: { value: 8.075, uncertainty: 0.070 }
  Ag: { value: 0.008, type: information }     # reported, never fails a batch
  Zn: { value: }                              # placeholder — skipped and announced
```

Samples are matched **by name**, since labs type a CRM as LCS, QC or Sample
interchangeably. Units are converted, never assumed. `type: information` values
are shown but cannot fail anyone's batch.

Six skeletons for the geological glasses (NIST SRM 610/612, BCR-2G, BHVO-2G,
BIR-1G, GSD-1G) ship as `*.yaml.example` with every value empty — fill in the
ones you measure and rename to `.yaml`. See
[`configs/crm/README.md`](../configs/crm/README.md).

---

## 7. Automation

```bash
icpms-qc check batch.csv --rules mylab --out reports/$(date +%F)
echo $?     # 0 = pass, 2 = QC failures, 1 = error
```

Because failures exit non-zero, this drops straight into a cron job, a Makefile
or CI. Read `qc_report.json` for the detail — it is schema-versioned and treated
as a public API.

```python
import json
d = json.load(open("out/qc_report.json"))
d["verdict"]                                    # "PASS" | "FAIL"
[c["check_id"] for c in d["checks"] if c["outcome"] == "FAIL"]
d["batch"]["warnings"]                          # anything the parser could not map
```

---

## 8. Laser ablation runs

If you have a laser log, pass it and icpms-qc will audit whether the results and the
laser's own record describe the same run — patterns fired vs rows reported, names
position by position, ablation durations:

```bash
icpms-qc check reduced_results.csv --laser-log LaserLog.csv
```

icpms-qc does not perform the alignment (that's reduction, and pewpew/Ilaps/iolite/
laserTRAM already do it). It only checks whether the answer survives comparison.
Without `--laser-log` the check reports `NOT_EVALUATED`, which is the normal state
for solution-mode work.

---

## Getting stuck

- **"no analyte columns matched"** → wrong template. Run `icpms-qc inspect` and
  compare the real column names against the template's `analyte_conc_pattern`.
- **`unrecognized sample type 'X' -> OTHER`** → add `X` to the template's
  `sample_type_vocab`. Checks needing that type will say `NOT_EVALUATED` until you do.
- **Everything `NOT_EVALUATED`** → the batch genuinely lacks those QC samples, or
  the sample-type vocabulary isn't mapped. The reasons tell you which.
- **Mojibake in element names (`µg/L`)** → the export is cp1252, not UTF-8. Set
  `encoding: cp1252` in your template; icpms-qc falls back automatically and warns.

Found a layout icpms-qc can't read? A **redacted** export (fake sample names, real
column structure) plus the software version is the single most useful thing you
can contribute.
