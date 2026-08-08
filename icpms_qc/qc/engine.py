"""Rule-pack loader + check dispatcher.

Loads a rule pack YAML (configs/*.yaml), runs every enabled check from the
catalog in catalog order, returns the ordered CheckResult list. The engine adds
nothing clever — clever lives in the checks; policy lives in the pack.

Pack layout:
    pack: <name>
    shared:                 # merged into every check's params (check wins)
      loq_ppb: {default: 0.1}
    checks:
      <check_id>: {enabled: true, params: {...}, verify: "..."}
"""
from __future__ import annotations

from pathlib import Path

import yaml

from icpms_qc.model import Batch
from icpms_qc.qc import checks
from icpms_qc.qc.checks import CheckResult, Outcome

_REPO_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


def resolve_pack_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix in {".yaml", ".yml"}:
        if p.exists():
            return p
        raise FileNotFoundError(f"rule pack file not found: {p}")
    for base in (_REPO_CONFIG_DIR, Path.cwd() / "configs"):
        cand = base / f"{name_or_path}.yaml"
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"rule pack '{name_or_path}' not found in {_REPO_CONFIG_DIR} or ./configs")


def load_pack(name_or_path: str) -> dict:
    data = yaml.safe_load(resolve_pack_path(name_or_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "checks" not in data:
        raise ValueError(f"rule pack {name_or_path!r} has no 'checks' section")
    return data


def run(batch: Batch, rules: str = "epa6020b") -> list[CheckResult]:
    pack = load_pack(rules)
    shared = pack.get("shared") or {}
    results: list[CheckResult] = []
    for check_id, fn in checks.CATALOG.items():
        cfg = (pack.get("checks") or {}).get(check_id)
        if not cfg or not cfg.get("enabled", False):
            continue
        params = {**shared, **(cfg.get("params") or {})}
        try:
            res = fn(batch, params)
        except Exception as exc:                     # a broken check must be loud, not fatal
            res = CheckResult(check_id, Outcome.NOT_EVALUATED, reason=f"check error: {exc!r}")
        res.verify = str(cfg.get("verify", ""))
        results.append(res)
    return results


def verdict(results: list[CheckResult]) -> str:
    """Batch verdict: FAIL if any check failed, else PASS.

    NOT_EVALUATED / WARN never fail a batch silently — they are surfaced in the
    report and JSON so the reviewer decides.
    """
    return "FAIL" if any(r.outcome == Outcome.FAIL for r in results) else "PASS"
