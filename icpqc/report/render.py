"""Report writers: CheckResult[] + Batch → audit-ready HTML + stable JSON sidecar.

JSON sidecar is the public contract lab automation and AI agents consume —
schema-versioned; changing it is an API change. HTML is a single self-contained
file (inline CSS, no external assets), print-clean for PDF-via-browser.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from icpqc import __version__
from icpqc.model import Batch
from icpqc.qc.checks import CheckResult, Outcome
from icpqc.qc.engine import verdict as _verdict

JSON_SCHEMA_VERSION = "0.1"

_BADGE = {
    Outcome.PASS: ("PASS", "#16a34a"),
    Outcome.FAIL: ("FAIL", "#dc2626"),
    Outcome.WARN: ("WARN", "#d97706"),
    Outcome.NOT_EVALUATED: ("NOT EVALUATED", "#6b7280"),
}

_TYPE_COLOR = {
    "CAL_STD": "#7c3aed", "CAL_BLANK": "#a78bfa", "ICV": "#0ea5e9", "ICB": "#67e8f9",
    "CCV": "#0284c7", "CCB": "#7dd3fc", "MB": "#94a3b8", "LCS": "#059669",
    "SAMPLE": "#334155", "DUP": "#c2410c", "MS": "#b45309", "MSD": "#b45309",
    "SERIAL_DIL": "#9333ea", "POST_SPIKE": "#9333ea", "OTHER": "#dc2626",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _batch_summary(batch: Batch) -> dict:
    counts: dict[str, int] = {}
    for s in batch.samples:
        counts[s.type.value] = counts.get(s.type.value, 0) + 1
    return {
        "source": batch.source_path,
        "template": batch.template_id,
        "instrument_family": batch.instrument_family,
        "n_samples": len(batch.samples),
        "sample_type_counts": counts,
        "analytes": [a.label for a in batch.analytes],
        "istds": [a.label for a in batch.istds],
        # Additive to the 0.1 contract: `analytes` keeps its meaning (labels), and
        # the parsed identity of each is exposed alongside it. mass_shift is set
        # only on triple-quad MS/MS acquisitions.
        "analyte_detail": [
            {"label": a.label, "element": a.element, "mass": a.mass,
             "mass_shift": a.mass_shift, "mode": a.mode}
            for a in batch.analytes
        ],
        "warnings": batch.warnings,
    }


def to_json_dict(batch: Batch, results: list[CheckResult]) -> dict:
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "generated_by": f"icpqc {__version__}",
        "generated_at": _now_iso(),
        "batch": _batch_summary(batch),
        "verdict": _verdict(results),
        "checks": [
            {"check_id": r.check_id, "outcome": r.outcome.value, "reason": r.reason,
             "verify": r.verify, "details": r.details}
            for r in results
        ],
    }


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 0; padding: 2rem; color: #0f172a; background: #f8fafc; }
main { max-width: 72rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 .5rem; display: flex; align-items: center; gap: .5rem; }
.meta { color: #475569; font-size: .85rem; margin-bottom: 1.5rem; }
.badge { display: inline-block; padding: .15rem .6rem; border-radius: 999px;
         color: #fff; font-weight: 600; font-size: .8rem; letter-spacing: .02em; }
.verdict { font-size: 1rem; padding: .35rem 1rem; }
.chip { display: inline-block; padding: .05rem .5rem; border-radius: 999px;
        color: #fff; font-size: .75rem; }
table { border-collapse: collapse; width: 100%; background: #fff;
        border: 1px solid #e2e8f0; font-size: .85rem; }
th, td { padding: .35rem .6rem; text-align: left; border-bottom: 1px solid #e2e8f0; }
th { background: #f1f5f9; font-weight: 600; }
tr:nth-child(even) td { background: #f8fafc; }
td.ok-true { color: #16a34a; font-weight: 600; }
td.ok-false { color: #dc2626; font-weight: 700; }
.reason { color: #475569; font-size: .85rem; margin: .25rem 0; }
.verify { color: #92400e; background: #fef3c7; border-radius: .375rem;
          padding: .3rem .6rem; font-size: .78rem; margin: .35rem 0 .6rem; display: inline-block; }
.warnbox { background: #fef2f2; border: 1px solid #fecaca; border-radius: .5rem;
           padding: .6rem 1rem; margin: 1rem 0; font-size: .85rem; }
footer { margin-top: 2.5rem; color: #64748b; font-size: .78rem;
         border-top: 1px solid #e2e8f0; padding-top: .75rem; }
.tablewrap { overflow-x: auto; }
@media print { body { background: #fff; padding: 0; } .tablewrap { overflow: visible; } }
"""


def _esc(v) -> str:
    return html.escape(str(v))


def _badge(outcome: Outcome, cls: str = "badge") -> str:
    label, color = _BADGE[outcome]
    return f'<span class="{cls}" style="background:{color}">{label}</span>'


def _details_table(details: list[dict]) -> str:
    if not details:
        return ""
    cols: list[str] = []
    for row in details:
        for k in row:
            if k not in cols:
                cols.append(k)
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body_rows = []
    for row in details:
        cells = []
        for c in cols:
            v = row.get(c)
            if c == "ok":
                if v is True:
                    cells.append('<td class="ok-true">pass</td>')
                elif v is False:
                    cells.append('<td class="ok-false">FAIL</td>')
                else:
                    cells.append("<td>&ndash;</td>")
            else:
                cells.append(f"<td>{'&ndash;' if v is None else _esc(v)}</td>")
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return (f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table></div>')


def to_html(batch: Batch, results: list[CheckResult]) -> str:
    b = _batch_summary(batch)
    overall = _verdict(results)
    overall_badge = _badge(Outcome.PASS if overall == "PASS" else Outcome.FAIL,
                           "badge verdict")

    parts: list[str] = []
    parts.append(f"<!doctype html><html><head><meta charset='utf-8'>"
                 f"<title>icpqc report — {_esc(Path(batch.source_path).name)}</title>"
                 f"<style>{_CSS}</style></head><body><main>")
    parts.append(f"<h1>icpqc QC report {overall_badge}</h1>")
    parts.append(
        f"<div class='meta'>source: <b>{_esc(batch.source_path)}</b> &middot; "
        f"template: {_esc(b['template'])} &middot; instrument: {_esc(b['instrument_family'])} "
        f"&middot; samples: {b['n_samples']} &middot; analytes: {len(b['analytes'])} "
        f"&middot; generated: {_esc(_now_iso())} by icpqc {_esc(__version__)}</div>")

    if batch.warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in batch.warnings)
        parts.append(f"<div class='warnbox'><b>Parser warnings</b><ul>{items}</ul></div>")

    # Analytes as icpqc understood them. A reviewer's fastest way to catch a
    # mis-read header: an element column that came back blank here was not
    # recognized, and every element-keyed check has been skipping it.
    parts.append("<h2>Analytes</h2>")
    a_rows = []
    for a in batch.analytes + batch.istds:
        istd = a in batch.istds
        mz = "&ndash;" if a.mass is None else (
            f"{a.mass} &rarr; {a.mass_shift}" if a.mass_shift is not None else str(a.mass))
        a_rows.append(
            f"<tr><td>{_esc(a.label)}</td><td>{_esc(a.element or '—')}</td>"
            f"<td>{mz}</td><td>{_esc(a.mode or '—')}</td>"
            f"<td>{'ISTD' if istd else 'analyte'}</td></tr>")
    parts.append("<div class='tablewrap'><table><thead><tr><th>label</th>"
                 "<th>element</th><th>m/z</th><th>cell mode</th><th>role</th>"
                 "</tr></thead><tbody>" + "".join(a_rows) + "</tbody></table></div>")

    parts.append("<h2>Sequence</h2>")
    seq_rows = []
    for s in batch.samples:
        color = _TYPE_COLOR.get(s.type.value, "#334155")
        seq_rows.append(
            f"<tr><td>{s.seq_index}</td><td>{_esc(s.name)}</td>"
            f"<td><span class='chip' style='background:{color}'>{_esc(s.type.value)}</span></td>"
            f"<td>{'&ndash;' if s.level is None else _esc(f'{s.level:g}')}</td></tr>")
    parts.append("<div class='tablewrap'><table><thead><tr><th>#</th><th>sample</th>"
                 "<th>type</th><th>level</th></tr></thead><tbody>"
                 + "".join(seq_rows) + "</tbody></table></div>")

    for r in results:
        parts.append(f"<h2>{_esc(r.check_id)} {_badge(r.outcome)}</h2>")
        if r.reason:
            parts.append(f"<div class='reason'>{_esc(r.reason)}</div>")
        if r.verify:
            parts.append(f"<div class='verify'>verify against method text: {_esc(r.verify)}</div>")
        parts.append(_details_table(r.details))

    parts.append(
        "<footer>Generated by icpqc (MIT). Rule-pack thresholds are typical defaults — "
        "verify every limit against the current method text before compliance use. "
        "Not affiliated with any instrument vendor.</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


def write(batch: Batch, results: list[CheckResult], out_dir: str = "out") -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / "qc_report.html"
    json_path = out / "qc_report.json"
    html_path.write_text(to_html(batch, results), encoding="utf-8")
    json_path.write_text(json.dumps(to_json_dict(batch, results), indent=2),
                         encoding="utf-8")
    return html_path, json_path
