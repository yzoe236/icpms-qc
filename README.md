# icpqc

*(working name — check PyPI/GitHub availability before first release)*

**Post-run QC engine and audit-ready compliance reporter for ICP-MS batch exports.**

Point it at your instrument's quantitative batch export (CSV) and get: pass/fail flags for
every QC element your method requires, an audit-ready QC report, and (roadmap) cross-batch
drift trends — driven by versioned, editable rule packs (EPA 6020B-style, EPA 200.8-style,
ISO 17294-2, or your lab's own SOP).

> Works with **Agilent ICP-MS MassHunter batch exports** today. Thermo Qtegra and
> PerkinElmer Syngistix export support is on the roadmap — want it sooner? Open an issue
> with a redacted sample export.
>
> This project is not affiliated with or endorsed by any instrument vendor. MassHunter is a
> trademark of Agilent Technologies. Method rule packs ship with *typical default* limits —
> **verify every threshold against the current text of your method before compliance use.**

## Why this exists

Vendor software already does run-time QC during acquisition (outlier flags, auto-reruns).
What routine labs still do by hand afterwards — in Excel, every batch — is the part this
tool automates:

| | Vendor run-time QC | icpqc |
|---|---|---|
| When it runs | during acquisition | post-run, re-runnable any time |
| Cross-batch trends (drift, ISTD decay history) | ✗ | roadmap v0.2 |
| One-command audit-ready QC report | partial | ✓ HTML + JSON sidecar |
| QC rules as reviewable, versioned files | template-bound | ✓ YAML rule packs in git |
| Multi-vendor, one workflow | ✗ | roadmap v0.3 |
| Automation / AI-agent friendly output | ✗ | ✓ stable JSON contract |
| License | commercial | MIT, free |

## Quick start

```bash
pip install .                      # from a clone; PyPI release pending name check
icpqc check my_batch_export.csv --rules epa6020b --template masshunter_quant_wide
# → out/qc_report.html  (human)  +  out/qc_report.json  (machines/agents)
# exit code: 0 = all pass, 2 = QC failures, 1 = error  (CI/automation friendly)
```

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
(every N analyses + end of run) · ICB/CCB and method blanks vs LOQ · internal-standard
drift vs the ICAL reference · LCS recovery · duplicate RPD · MS/MSD recovery + RPD ·
serial-dilution agreement · required-QC presence. Each check reports
PASS / FAIL / WARN / NOT_EVALUATED — a check that can't run says so loudly, because
silence is how QC reports lie.

## Design in one paragraph

Exports vary wildly across MassHunter versions and report templates, so parsing is
**template-driven**: a small YAML mapping (`configs/*.template.yaml`) turns your lab's
export layout into a canonical batch model, and the QC engine only ever sees the canonical
model. Adding an instrument or a new export layout = contributing a template file, not
forking the engine. QC logic itself is a set of small, method-agnostic checks that rule
packs (`configs/*.yaml`) parameterize with your method's limits.

## Status

**v0.1 implemented**: reference-template parser, 13-check engine, HTML + JSON reports,
CLI — test suite green. Current phase: field-testing against real-world export layouts.
The single most useful contribution: a **redacted** export from your lab (fake sample
names, real column layout) + the software version that produced it.

## Roadmap

- **v0.2** — batch history store (SQLite) + cross-batch trend charts (instrument drift, ISTD decay)
- **v0.3** — Thermo Qtegra export support; PerkinElmer Syngistix export support
- **later** — sequence-design linting before you press Run, LIMS-friendly outputs

## Contributing

Issues and PRs welcome. Never post client-identifiable data — synthetic or redacted
fixtures only (`tools/gen_synthetic_data.py` shows the reference layout).

## License

MIT © 2026 Linhan Li
