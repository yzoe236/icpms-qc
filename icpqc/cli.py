"""icpqc command-line interface.

    icpqc check <export.csv> --rules epa6020b --template masshunter_quant_wide [--out DIR]

Exit codes: 0 = all checks pass, 2 = QC failures present, 1 = error.
Automation/agent friendly: the JSON sidecar is the API; stdout stays terse.
"""
from __future__ import annotations

import argparse
import sys

from icpqc.io import masshunter
from icpqc.qc import engine
from icpqc.qc.checks import Outcome
from icpqc.report import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icpqc", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run QC checks on a batch export")
    check.add_argument("export_csv")
    check.add_argument("--rules", default="epa6020b", help="rule pack name or path to YAML")
    check.add_argument("--template", default="masshunter_quant_wide",
                       help="export-layout template name or path to YAML")
    check.add_argument("--out", default="out", help="output directory for reports")

    args = parser.parse_args(argv)

    if args.command == "check":
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

    return 1


if __name__ == "__main__":
    sys.exit(main())
