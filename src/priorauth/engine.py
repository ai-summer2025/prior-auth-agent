"""Deterministic, three-valued evaluation of a request against a policy.

A pathway is met when all of its conditions are met; it is not met when any
condition is not met; otherwise it is missing information. The policy is met
when any pathway is met. Missing facts are never assumed.
"""
from __future__ import annotations

from typing import Any, List, Optional

from .models import ConditionResult, Determination, PathwayResult, Request
from .policies import load_policies, policy_for


def _check(op: str, actual: Any, expected: Any) -> bool:
    if op == "any_in":
        return bool(set(actual or []) & set(expected))
    if op == "none_in":
        return not (set(actual or []) & set(expected))
    if op == "==":
        return actual == expected
    a, e = float(actual), float(expected)
    return {">=": a >= e, "<=": a <= e, ">": a > e, "<": a < e}[op]


def evaluate_pathway(pw: dict, facts: dict) -> PathwayResult:
    results: List[ConditionResult] = []
    for c in pw["conditions"]:
        if c["fact"] not in facts or facts[c["fact"]] is None:
            results.append(ConditionResult(c["fact"], c["op"], c["value"], None, "missing"))
            continue
        actual = facts[c["fact"]]
        try:
            ok = _check(c["op"], actual, c["value"])
        except (TypeError, ValueError):
            results.append(ConditionResult(c["fact"], c["op"], c["value"], actual, "missing"))
            continue
        results.append(ConditionResult(c["fact"], c["op"], c["value"], actual, "met" if ok else "not_met"))
    statuses = {r.status for r in results}
    status = "not_met" if "not_met" in statuses else "missing" if "missing" in statuses else "met"
    return PathwayResult(pw["name"], status, results)


def determine(req: Request, policies: Optional[List[dict]] = None) -> Determination:
    policies = policies if policies is not None else load_policies()
    p = policy_for(req.service_code, policies)
    if p is None:
        return Determination(req.id, None, None, "no_policy",
                             f"No policy in this repository covers service code {req.service_code}.")
    checklist = list(p.get("documentation", []))
    if not any(dx.upper().startswith(prefix) for dx in req.diagnosis_codes for prefix in p["diagnosis_prefixes"]):
        return Determination(req.id, p["id"], p["title"], "does_not_meet",
                             f"Diagnosis codes {', '.join(req.diagnosis_codes) or '(none)'} are not among the "
                             f"diagnoses this policy covers ({', '.join(p['diagnosis_prefixes'])}).",
                             documentation_checklist=checklist, basis=p["basis"])
    pathways = [evaluate_pathway(pw, req.facts) for pw in p["pathways"]]
    met = [pw for pw in pathways if pw.status == "met"]
    if met:
        return Determination(req.id, p["id"], p["title"], "meets_criteria",
                             f"Meets criteria via: {met[0].name}.", pathways, [], checklist, p["basis"])
    open_pathways = [pw for pw in pathways if pw.status == "missing"]
    if open_pathways:
        missing = sorted({c.fact for pw in open_pathways for c in pw.conditions if c.status == "missing"})
        return Determination(req.id, p["id"], p["title"], "missing_information",
                             "Cannot determine yet. Provide: " + ", ".join(missing) + ".",
                             pathways, missing, checklist, p["basis"])
    reasons = "; ".join(
        f"{pw.name}: " + ", ".join(f"{c.fact} {c.op} {c.expected} (has {c.actual})"
                                     for c in pw.conditions if c.status == "not_met")
        for pw in pathways)
    return Determination(req.id, p["id"], p["title"], "does_not_meet", "No pathway met. " + reasons,
                         pathways, [], checklist, p["basis"])
