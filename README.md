# icpms-qc — the QC check machine for ICP-MS batches

**Point it at your batch export. Get back a report that says whether the data can be
reported, and why.**

```bash
pip install .
icpms-qc check my_batch.csv
```

That is the whole thing. No template to choose, no options to learn — it works out what
your file is, reads it, and writes `out/qc_report.html` for you and `out/qc_report.json`
for your scripts.

```
  read as: masshunter · masshunter_conc_2row · paired with Count_12052024.csv
icpms-qc FAIL: Conc_12052024.csv
  [         PASS] cal_linearity
  [         FAIL] precision_rsd   - 76 failing row(s)
  [         WARN] instrument_flags - 1128 objection(s) from the instrument software
  [NOT_EVALUATED] ccv_recovery    - no CCV samples in batch
  report: out/qc_report.html
```

Exit code 0 = everything passed, 2 = QC failures, 1 = error. Drop it in a cron job.

## Why

Your instrument software will tell you the CCV recovered at 87%. It will not tell you
whether the batch is usable, which samples the failure reaches, or what to check first.
**Instruments produce numbers. They do not produce assurance.** Somebody has to look at
the run and say "I have reviewed this, it can be reported" — and in most labs that
somebody is one experienced person, one batch at a time.

icpms-qc automates the reviewable part: 20 QC criteria evaluated, with the numbers behind
every verdict, in a report you can hand to someone else.

## What it reads

| | |
|---|---|
| **Agilent MassHunter** | batch exports (CSV), single-quad 7700/7800/7900 and triple-quad MS/MS (`31 -> 47 P [O2]`, 8800/8900), collision and reaction cell modes |
| **Thermo Element 2 / XR** | ASCII exports (`.ASC`) — point it at the **run folder**, since the Element writes a file per sample |
| **Count + Conc pairs** | `Conc_0816.csv` with `Count_0816.csv` beside it is paired automatically |

Tested against **165 real MassHunter exports and 212 real Element files** — 99%+ read
without being told anything about them. Thermo Qtegra and PerkinElmer Syngistix are on
the roadmap.

## Something not working?

Pick whichever costs you least:

| | |
|---|---|
| [**It could not read my export**](https://github.com/yzoe236/icpms-qc/issues/new?template=layout.yml) | a short form — instrument, software version, and a fingerprint |
| [**A check got it wrong**](https://github.com/yzoe236/icpms-qc/issues/new?template=verdict.yml) | passed something bad, or failed something fine |
| [**yzoe236@gmail.com**](mailto:yzoe236@gmail.com) | if you would rather not open an issue at all |

**You never need to send data.** `icpms-qc inspect yourfile.csv` prints a layout
fingerprint that is already safe to share — column headers and column kinds, never a
single measurement, with free text outside lab vocabulary masked. Paste that.

And if a run produces warnings, the tool hands you a link with the form **already filled
in** from what it just printed. The only thing left to type is your software version:

```
Something it could not read? One click, form already filled in:
  https://github.com/yzoe236/icpms-qc/issues/new?template=layout.yml&title=…&what=…
```

Every layout somebody sends is one more lab this works for out of the box. That is
genuinely the most useful thing you can contribute.

## Who this is for (and who it isn't)

Let's be direct about the landscape, because overselling a QC tool is its own kind of QC
failure.

**Vendor software already does the calculation well.** Agilent's Intelligent Sequence ships
templates for EPA 200.8, 6020 and ISO 17294 and evaluates ISTD recovery, calibration
linearity, %RSD and blank levels during acquisition. **A regulated lab running a validated
LIMS** (LabWare and its peers) already has batch QC enforcement, control charts, audit
trails and scheduled compliance reporting — and changing tools there means re-validation
and an audit. If that's you, icpms-qc is not worth your trouble, and you should not switch.

**icpms-qc is for the labs in between:** a shared instrument facility, a research group, a
lab that owns an ICP-MS but no LIMS and no dedicated QC infrastructure — where the review
is real work done by a person, and the record of it is a spreadsheet on someone's desktop.

What it gives you there:

| | |
|---|---|
| **Runs after the fact** | on the export, any time — re-review a two-year-old batch against today's limits |
| **Rules are files you can read** | YAML rule packs in git; your QC policy becomes reviewable and diffable |
| **Says what it could not check** | `NOT_EVALUATED` with a reason, never a silent pass |
| **Shows the numbers** | every verdict comes with the rows behind it, so a reviewer can disagree |
| **Machine-readable** | stable JSON sidecar for automation and AI agents |
| **MIT** | yours to fork, audit and modify |

**It does not** control the instrument, reprocess raw signal, or replace a validated
workflow. And it does not certify anything — you remain the person who signs off.

## Quick start

```bash
pip install .                      # from a clone; PyPI release pending name check
icpms-qc check my_batch_export.csv --rules epa6020b --template masshunter_quant_wide
# → out/qc_report.html  (human)  +  out/qc_report.json  (machines/agents)
# exit code: 0 = all pass, 2 = QC failures, 1 = error  (CI/automation friendly)
```

📖 **[docs/USAGE.md](docs/USAGE.md)** — full walkthrough: your own export, reading the
report, adapting the rules to your lab, reference materials, automation.

## Your export doesn't match a template?

Exports differ by vendor, software version, and report template, so the honest answer
to "will it read my file?" used to be "after you write a YAML template." Now:

```bash
icpms-qc inspect my_export.csv                            # what does my layout look like?
icpms-qc template-from-header my_export.csv --id my_lab   # draft a template for it
icpms-qc template-from-header my_export.csv --id my_lab --accept
```

`template-from-header` extracts a **layout fingerprint** — column headers, per-column
kind, categorical vocabularies — asks a language model to draft the template, then
**validates the draft by parsing your actual export** and reports what it understood
and what it did not. Nothing is written until you pass `--accept`.

Three things this deliberately does *not* do:

- **It never sends measurements.** The fingerprint carries layout, not values; free
  text outside lab vocabulary is masked, so client sample names stay on your machine.
  Run `icpms-qc inspect` to see exactly what would be sent. (`--include-names` opts out,
  and tells you it did.)
- **The model never judges QC.** It authors a template, once. After you accept it, the
  layout is a plain YAML file and every future run is deterministic — the same export
  produces the same report in three years, which is the point of a compliance tool.
- **It never guesses quietly.** Ambiguous sample types are left unmapped and reported,
  because a loud `NOT_EVALUATED` is worth more than a confident wrong answer.

Needs the [`claude` CLI](https://claude.com/claude-code) or `ANTHROPIC_API_KEY`. Without
either, `icpms-qc inspect` still works — hand the fingerprint to any model, or write the
template yourself.

## Quick start (continued)

No real data at hand? Generate a synthetic demo batch:

```bash
python tools/gen_synthetic_data.py demo_batch.csv
icpms-qc check demo_batch.csv --rules epa6020b
python tools/gen_synthetic_data.py bad_batch.csv --violations   # see a failing report
```

## What v0.1 checks

Calibration linearity and low-standard recovery · ICV/CCV recovery · CCV cadence
(every N analyses + end of run) · ICB/CCB and method blanks vs LOQ · **detection limits
implied by the run's own blank scatter** (3σ/10σ, audited against the LOQ your pack
declares) · internal-standard drift vs the ICAL reference · LCS recovery · **certified
reference material recovery, per certified element** · duplicate RPD · MS/MSD recovery +
RPD · serial-dilution agreement · required-QC presence. Each check reports
PASS / FAIL / WARN / NOT_EVALUATED — a check that can't run says so loudly, because
silence is how QC reports lie.

**Precision is a first-class check, not an afterthought.** A run can recover every
standard perfectly and still be unusable because the signal would not sit still, and the
accuracy checks cannot see that. `precision_rsd` reads the %RSD your export already
carries — gated on signal level, because the RSD of a blank is counting noise rather than
a finding. Validating against two real batches found icpms-qc silently reporting nothing
while the instrument software itself had raised 1218 objections, all of them precision or
calibration; that is the hole this closes.

**It can check the number against the counts it came from.** `quant_crosscheck`
rebuilds the calibration from the standards in the same export, predicts each sample from
its own raw intensities, and compares. The instrument software cannot raise this, because
it *is* what produced the number — it applies the parameters it was given and does not
doubt them. A dilution factor typed one digit wrong, an internal standard assigned to the
wrong analyte, a stale calibration carried into a new batch: all produce confident,
well-formatted, wrong results that every other QC check passes. It reports one thing only, and that restraint is the point. An export does not carry the
regression weighting, the curve type, the excluded standards or the interference-correction
equations, so individual samples differ for reasons that say nothing about correctness.
What survives every one of those unknowns is a *ratio*: when several analytes are out by
the **same** factor, something scaled the sample — a dilution, a unit, the wrong
calibration. One analyte alone at a constant offset is per-mass arithmetic (an
interference correction, typically) and is shown rather than blamed.

Counts and concentrations usually live in two files — `Count_0816.csv` beside
`Conc_0816.csv`. Pass the companion with `--counts` and they are paired by sequence:

```bash
icpms-qc check Conc_0816.csv --counts Count_0816.csv --template masshunter_conc_2row
```

**The instrument's own verdict is carried, not discarded.** MassHunter writes its QC
objections into the export. `instrument_flags` parses and reports them alongside icpms-qc's
own conclusions — they are the vendor's thresholds, so they are reported rather than
decisive (`on_flag: fail` makes them binding), and where the two disagree, that
disagreement is exactly what a reviewer needs to see.

Non-detects are treated as results, not as missing data. `<0.05` bounds the truth from
above: a blank censored *below* its threshold passes on merit, one censored above it is
reported as undecidable, and a standard that came back non-detect fails on its upper
bound. Reading `<0.05` as "no value" makes a clean blank and an unmeasured blank produce
the same report.


### Reference materials

A CRM certifies dozens of elements at dozens of different values, so it does not fit the
single Level column an export carries. Certified values live in `icpms_qc/configs/crm/*.yaml` —
one file per material, matched to samples by name, versioned in git next to the rule
packs. See [`icpms_qc/configs/crm/README.md`](icpms_qc/configs/crm/README.md).

```yaml
unit: ppb
match: { name_patterns: ['(?i)\bSRM\s*1640a\b'] }
certified:
  As: { value: 8.075, uncertainty: 0.070 }
```

Units are converted, never assumed; the certificate's own uncertainty is reported as
context but never used as the pass/fail criterion — that window is a QC policy decision
and belongs in the rule pack.

## Design in one paragraph

Exports vary wildly across MassHunter versions and report templates, so parsing is
**template-driven**: a small YAML mapping (`icpms_qc/configs/*.template.yaml`) turns your lab's
export layout into a canonical batch model, and the QC engine only ever sees the canonical
model. Adding an instrument or a new export layout = contributing a template file, not
forking the engine. QC logic itself is a set of small, method-agnostic checks that rule
packs (`icpms_qc/configs/*.yaml`) parameterize with your method's limits.

## Status

**v0.1 implemented**: auto-detecting parser (MassHunter + Thermo Element), 20-check
engine, CRM library, HTML + JSON reports, CLI — 131 tests green.
The single most useful contribution: a **redacted** export from your lab (fake sample
names, real column layout) + the software version that produced it.

## Roadmap

- **v0.2** — batch history store (SQLite) + cross-batch trend charts (instrument drift, ISTD decay)
- **v0.2** — **consensus and informational reference values.** The CRM library holds
  certified values today. Geological materials (BHVO-2, BCR-2, NIST 610) also carry
  consensus and informational values whose provenance matters as much as the number, so
  a value needs to record which compilation it came from and how much weight it carries.
- **v0.2** — carry the instrument's own reject flag (MassHunter's `Rjct` column) into the
  report instead of ignoring it; an auditable tool should not drop the vendor's own verdict
- **v0.3** — Thermo Qtegra export support; PerkinElmer Syngistix export support
- **later** — sequence-design linting before you press Run, LIMS-friendly outputs

## Prior art

[**ICPHuntR**](https://github.com/loeRl/ICPHuntR) (R, Lorenz Gfeller) reshapes MassHunter
export tables into tidy data frames and ships a table of reference-material values. It
stops where icpms-qc starts — it hands you a data frame to write your own analysis against,
rather than a verdict and a report — but it hit the same export-side problems first, and
three things here are better for having read it: triple-quad `mass -> mass` labels,
collision-cell mode as a first-class field, and detection limits estimated from the
post-calibration blanks. Independent tools converging on the same parsing headaches is
reasonable evidence the headaches are real.

## Contributing

Issues and PRs welcome. Never post client-identifiable data — synthetic or redacted
fixtures only (`tools/gen_synthetic_data.py` shows the reference layout).

## License

MIT © 2026 Linhan Li
