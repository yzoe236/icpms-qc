"""icpqc command-line interface.

    icpqc check <export.csv> --rules epa6020b --template masshunter_quant_wide [--out DIR]
    icpqc inspect <export.csv> [--include-names]
    icpqc template-from-header <export.csv> --id <name> [--accept] [--include-names]

Exit codes: 0 = all checks pass, 2 = QC failures present, 1 = error.
Automation/agent friendly: the JSON sidecar is the API; stdout stays terse.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from icpqc.io import masshunter
from icpqc.qc import engine
from icpqc.report import render


def _cmd_check(args) -> int:
    try:
        batch = masshunter.parse(args.export_csv, template=args.template)
        results = engine.run(batch, rules=args.rules)
        html_path, json_path = render.write(batch, results, out_dir=args.out)
    except (OSError, ValueError) as exc:
        print(f"icpqc: error: {exc}", file=sys.stderr)
        return 1

    verdict = engine.verdict(results)
    print(f"icpqc {verdict}: {args.export_csv}")
    for r in results:
        line = f"  [{r.outcome.value:>13}] {r.check_id}"
        if r.reason:
            line += f" - {r.reason}"
        print(line)
    for w in batch.warnings:
        print(f"  warning: {w}")
    print(f"  report: {html_path}")
    print(f"  json:   {json_path}")
    return 0 if verdict == "PASS" else 2


def _cmd_inspect(args) -> int:
    """Print the layout fingerprint — exactly what a model would be shown."""
    from icpqc.io import mapper
    try:
        fp = mapper.fingerprint(args.export_csv, include_names=args.include_names)
    except (OSError, ValueError) as exc:
        print(f"icpqc: error: {exc}", file=sys.stderr)
        return 1
    print(fp.to_json())
    if not args.include_names:
        print(f"\n# Measurements are never included. Free text outside lab "
              f"vocabulary is masked with {mapper._MASK}.", file=sys.stderr)
    return 0


def _cmd_template(args) -> int:
    """Draft a template for an unknown layout, validate it, then let a human accept."""
    from icpqc.io import mapper

    try:
        fp = mapper.fingerprint(args.export_csv, include_names=args.include_names)
    except (OSError, ValueError) as exc:
        print(f"icpqc: error: {exc}", file=sys.stderr)
        return 1

    print(f"icpqc: fingerprinting {args.export_csv}")
    print(f"  {fp.n_columns} columns · {fp.n_rows_scanned} rows scanned · "
          f"encoding {fp.encoding}")
    print(f"  sending layout only"
          f"{' (INCLUDING sample names — you asked)' if fp.names_included else ''}"
          f"; no measurement values leave this machine")
    print("icpqc: asking the model for a draft template …")

    try:
        tpl_yaml, v, _ = mapper.draft(args.export_csv, template_id=args.id,
                                      include_names=args.include_names)
    except RuntimeError as exc:
        print(f"icpqc: error: {exc}", file=sys.stderr)
        return 1

    if args.resolve and v.ok and v.unknown_types:
        plan = mapper.plan_resolution(tpl_yaml, args.export_csv)
        if plan.empty and not plan.skipped:
            print("icpqc: nothing to resolve — no unmapped types carry sample names")
        else:
            print("\nicpqc: some sample types stayed unmapped. Resolving them needs "
                  "the names of\n       the rows that carry them — QC and standard "
                  "rows, named by the lab.\n       Exactly this would be sent:")
            print(plan.describe())
            if plan.empty:
                print("       nothing left to disclose; skipping the resolution pass")
            else:
                print("icpqc: asking the model to resolve them …")
                tpl_yaml, v = mapper.resolve(tpl_yaml, args.export_csv, plan)

    print("\n─── draft template " + "─" * 52)
    print(tpl_yaml.rstrip())
    print("─── validation against this export " + "─" * 36)
    print(mapper.review_report(v, fp))
    print("─" * 70)

    if not v.ok:
        print("\nNot written. Fix the draft by hand, or rerun to try again.")
        return 1

    dest = Path(args.out) if args.out else (
        Path("configs") / f"{args.id or 'draft'}.template.yaml")
    if not args.accept:
        print(f"\nNothing written. Review the draft above, then rerun with --accept "
              f"to save it to {dest}.")
        print("A template is a claim about someone's data — it should be read by a "
              "person before it is trusted.")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(tpl_yaml, encoding="utf-8")
    print(f"\nwrote {dest}")
    print(f"now run:  icpqc check {args.export_csv} --template {dest.stem.replace('.template', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icpqc", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run QC checks on a batch export")
    check.add_argument("export_csv")
    check.add_argument("--rules", default="epa6020b", help="rule pack name or path to YAML")
    check.add_argument("--template", default="masshunter_quant_wide",
                       help="export-layout template name or path to YAML")
    check.add_argument("--out", default="out", help="output directory for reports")

    insp = sub.add_parser("inspect", help="print the layout fingerprint of an export")
    insp.add_argument("export_csv")
    insp.add_argument("--include-names", action="store_true",
                      help="include sample names verbatim (they may identify clients)")

    tpl = sub.add_parser("template-from-header",
                         help="draft a template for an unfamiliar export layout")
    tpl.add_argument("export_csv")
    tpl.add_argument("--id", help="template id, e.g. qtegra_wide")
    tpl.add_argument("--out", help="output path (default configs/<id>.template.yaml)")
    tpl.add_argument("--accept", action="store_true",
                     help="write the draft after you have reviewed it")
    tpl.add_argument("--include-names", action="store_true",
                     help="include sample names verbatim (they may identify clients)")
    tpl.add_argument("--resolve", action="store_true",
                     help="second pass: disclose the names of rows whose sample type "
                          "stayed unmapped (QC/standard rows only) to resolve them")

    args = parser.parse_args(argv)
    return {"check": _cmd_check,
            "inspect": _cmd_inspect,
            "template-from-header": _cmd_template}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
