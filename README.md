# icpqc

*(working name — check PyPI/GitHub availability before first release)*

**Post-run QC review for ICP-MS batch exports.**

Your instrument software will tell you the CCV recovered at 87%. It will not tell you
whether the batch is usable, which samples the failure reaches, or what you should check
first. **Instruments produce numbers. They do not produce assurance.** Somebody has to look
at the run and say "I've reviewed this, it can be reported" — and in most labs that
somebody is one experienced person, one batch at a time.

icpqc automates the reviewable part of that: point it at a batch export and get every QC
criterion evaluated, with the numbers behind each verdict, in a report you can hand to
someone else — driven by versioned, editable rule packs (EPA 6020B-style, EPA 200.8-style,
or your lab's own SOP).

> Works with **Agilent ICP-MS MassHunter batch exports** today — single-quad (7700/7800/
> 7900) and triple-quad MS/MS labels (`31 -> 47 P [O2]`, 8800/8900) alike, including
> collision/reaction cell modes. Thermo Qtegra and PerkinElmer Syngistix export support is
> on the roadmap — want it sooner? Open an issue with a redacted sample export.
>
> This project is not affiliated with or endorsed by any instrument vendor. MassHunter is a
> trademark of Agilent Technologies. Method rule packs ship with *typical default* limits —
> **verify every threshold against the current text of your method before compliance use.**

## Who this is for (and who it isn't)

Let's be direct about the landscape, because overselling a QC tool is its own kind of QC
failure.

**Vendor software already does the calculation well.** Agilent's Intelligent Sequence ships
templates for EPA 200.8, 6020 and ISO 17294 and evaluates ISTD recovery, calibration
linearity, %RSD and blank levels during acquisition. **A regulated lab running a validated
LIMS** (LabWare and its peers) already has batch QC enforcement, control charts, audit
trails and scheduled compliance reporting — and changing tools there means re-validation
and an audit. If that's you, icpqc is not worth your trouble, and you should not switch.

**icpqc is for the labs in between:** a shared instrument facility, a research group, a
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
icpqc check my_batch_export.csv --rules epa6020b --template masshunter_quant_wide
# → out/qc_report.html  (human)  +  out/qc_report.json  (machines/agents)
# exit code: 0 = all pass, 2 = QC failures, 1 = error  (CI/automation friendly)
```

📖 **[docs/USAGE.md](docs/USAGE.md)** — full walkthrough: your own export, reading the
report, adapting the rules to your lab, reference materials, automation.

## Your export doesn't match a template?

Exports differ by vendor, software version, and report template, so the honest answer
to "will it read my file?" used to be "after you write a YAML template." Now:

```bash
icpqc inspect my_export.csv                            # what does my layout look like?
icpqc template-from-header my_export.csv --id my_lab   # draft a template for it
icpqc template-from-header my_export.csv --id my_lab --accept
```

`template-from-header` extracts a **layout fingerprint** — column headers, per-column
kind, categorical vocabularies — asks a language model to draft the template, then
**validates the draft by parsing your actual export** and reports what it understood
and what it did not. Nothing is written until you pass `--accept`.

Three things this deliberately does *not* do:

- **It never sends measurements.** The fingerprint carries layout, not values; free
  text outside lab vocabulary is masked, so client sample names stay on your machine.
  Run `icpqc inspect` to see exactly what would be sent. (`--include-names` opts out,
  and tells you it did.)
- **The model never judges QC.** It authors a template, once. After you accept it, the
  layout is a plain YAML file and every future run is deterministic — the same export
  produces the same report in three years, which is the point of a compliance tool.
- **It never guesses quietly.** Ambiguous sample types are left unmapped and reported,
  because a loud `NOT_EVALUATED` is worth more than a confident wrong answer.

Needs the [`claude` CLI](https://claude.com/claude-code) or `ANTHROPIC_API_KEY`. Without
either, `icpqc inspect` still works — hand the fingerprint to any model, or write the
template yourself.

## Quick start (continued)

No real data at hand? Generate a synthetic demo batch:

```bash
python tools/gen_synthetic_data.py demo_batch.csv
icpqc check demo_batch.csv --rules epa6020b
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
a finding. Validating against two real batches found icpqc silently reporting nothing
while the instrument software itself had raised 1218 objections, all of them precision or
calibration; that is the hole this closes.

**The instrument's own verdict is carried, not discarded.** MassHunter writes its QC
objections into the export. `instrument_flags` parses and reports them alongside icpqc's
own conclusions — they are the vendor's thresholds, so they are reported rather than
decisive (`on_flag: fail` makes them binding), and where the two disagree, that
disagreement is exactly what a reviewer needs to see.

Non-detects are treated as results, not as missing data. `<0.05` bounds the truth from
above: a blank censored *below* its threshold passes on merit, one censored above it is
reported as undecidable, and a standard that came back non-detect fails on its upper
bound. Reading `<0.05` as "no value" makes a clean blank and an unmeasured blank produce
the same report.

### Laser ablation: auditing the two clocks

```bash
icpqc check reduced_results.csv --laser-log LaserLog.csv
```

The laser and the mass spectrometer are two instruments with two clocks, started by
two computers. Which counts belong to which ablation is always a *reconstruction* —
and when it slips (a lost trigger, a dropped sequence, an off-by-one), every
concentration after the slip is attributed to the wrong spot and nothing complains.

icpqc does not do the alignment — that is reduction, and
[pewpew](https://github.com/djdt/pewpew), [Ilaps](https://github.com/nikadilli/Ilaps-v2),
iolite and [laserTRAM](https://github.com/jlubbersgeo/laserTRAM-DB) already do it.
icpqc **audits** it: patterns fired vs rows reported, sample names position by
position, ablation durations against the run. That comparison needs no raw signal
at all, and today nothing else performs it.

**Granularity is not assumed.** One log sequence is one pattern; inside it are the
individual spots or lines. Which one a reduced row corresponds to depends on the
workflow, so `granularity: auto` settles it by whichever count matches — and if
neither matches, *that* is the finding, reported with both numbers.

### Reference materials

A CRM certifies dozens of elements at dozens of different values, so it does not fit the
single Level column an export carries. Certified values live in `configs/crm/*.yaml` —
one file per material, matched to samples by name, versioned in git next to the rule
packs. See [`configs/crm/README.md`](configs/crm/README.md).

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
**template-driven**: a small YAML mapping (`configs/*.template.yaml`) turns your lab's
export layout into a canonical batch model, and the QC engine only ever sees the canonical
model. Adding an instrument or a new export layout = contributing a template file, not
forking the engine. QC logic itself is a set of small, method-agnostic checks that rule
packs (`configs/*.yaml`) parameterize with your method's limits.

## Status

**v0.1 implemented**: reference-template parser, 20-check engine, CRM library, laser-log
auditing, HTML + JSON reports, CLI — test suite green. Current phase: field-testing against real-world layouts.
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
stops where icpqc starts — it hands you a data frame to write your own analysis against,
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
