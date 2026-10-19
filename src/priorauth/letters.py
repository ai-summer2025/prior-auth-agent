"""Letters built only from the determination and the request's facts.

Nothing in a letter is generated free-form: every clinical statement comes from a
fact in the request (with its quoted evidence when it was extracted from a note),
and every criterion comes from the policy. This keeps letters auditable.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .models import Determination, Request


def _fact_lines(req: Request, d: Determination) -> list:
    lines = []
    for pw in d.pathways:
        if pw.status != "met":
            continue
        for c in pw.conditions:
            val = c.actual if not isinstance(c.actual, list) else ", ".join(
                v.replace("_", " ") for v in c.actual)
            ev = req.evidence.get(c.fact)
            lines.append(f"- {c.fact.replace('_', ' ')}: {val}" + (f' (documented: "{ev}")' if ev else ""))
        break
    return lines


def request_letter(req: Request, d: Determination, today: Optional[date] = None) -> str:
    if d.outcome != "meets_criteria":
        raise ValueError("request letters are generated only for requests that meet criteria")
    met = next(pw for pw in d.pathways if pw.status == "met")
    return "\n".join([
        f"Date: {(today or date.today()).isoformat()}",
        f"Re: Prior authorization request {req.id} - {d.policy_title} (service {req.service_code})",
        f"Diagnoses: {', '.join(req.diagnosis_codes)}", "",
        f"This request meets the coverage criteria under the pathway \"{met.name}\".", "",
        "Supporting clinical facts:", *_fact_lines(req, d), "",
        f"Clinical basis: {d.basis}.", "",
        "Enclosed documentation:", *[f"- {doc}" for doc in d.documentation_checklist], "",
        "[Ordering clinician name, credentials, NPI, contact]",
    ])


def missing_info_message(req: Request, d: Determination) -> str:
    if d.outcome != "missing_information":
        raise ValueError("only for missing_information determinations")
    lines = [f"Request {req.id} ({d.policy_title}) cannot be submitted yet.", "",
             "Please document the following in the chart:"]
    for pw in d.pathways:
        if pw.status == "missing":
            lines.append(f"- For \"{pw.name}\": " + ", ".join(
                c.fact.replace("_", " ") for c in pw.conditions if c.status == "missing"))
    lines += ["", "Documentation the payer will expect:", *[f"- {doc}" for doc in d.documentation_checklist]]
    return "\n".join(lines)


def appeal_points(req: Request, d: Determination) -> str:
    """For a denial the clinic believes is wrong: restate the met pathway, or the gap to close."""
    if d.outcome == "meets_criteria":
        return "\n".join(["Appeal points (criteria are met according to the policy):", *_fact_lines(req, d),
                          f"Basis: {d.basis}."])
    return ("Criteria are not met on the facts provided; an appeal is unlikely to succeed unless new "
            "information addresses: " + d.summary)
