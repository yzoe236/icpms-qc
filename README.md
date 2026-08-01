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
