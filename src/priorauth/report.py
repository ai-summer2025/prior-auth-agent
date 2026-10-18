from __future__ import annotations

from .models import Determination, Request

LABEL = {"meets_criteria": "MEETS CRITERIA", "missing_information": "MISSING INFORMATION",
         "does_not_meet": "DOES NOT MEET CRITERIA", "no_policy": "NO POLICY"}
MARK = {"met": "[x]", "not_met": "[ ]", "missing": "[?]"}


def to_markdown(req: Request, d: Determination) -> str:
    out = [f"# Prior authorization review: {req.id}", "",
           f"**{LABEL[d.outcome]}**" + (f" - {d.policy_title}" if d.policy_title else ""), "", d.summary, ""]
    for pw in d.pathways:
        out.append(f"**Pathway: {pw.name}** ({pw.status})")
        for c in pw.conditions:
            actual = "not documented" if c.actual is None else c.actual
            ev = req.evidence.get(c.fact)
            out.append(f"- {MARK[c.status]} {c.fact} {c.op} {c.expected}: {actual}" + (f' ("{ev}")' if ev else ""))
        out.append("")
    if d.documentation_checklist:
        out += ["**Documentation to attach:**"] + [f"- {x}" for x in d.documentation_checklist] + [""]
    if d.basis:
        out.append(f"_Policy basis: {d.basis}. Illustrative policy, not any payer's actual coverage policy._")
    return "\n".join(out)
