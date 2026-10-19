from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Request:
    id: str
    service_code: str
    diagnosis_codes: List[str]
    facts: Dict[str, Any] = field(default_factory=dict)
    attached_documents: List[str] = field(default_factory=list)
    patient_age: Optional[int] = None
    evidence: Dict[str, str] = field(default_factory=dict)  # fact -> quoted note text, when extracted


@dataclass
class ConditionResult:
    fact: str
    op: str
    expected: Any
    actual: Any
    status: str  # met | not_met | missing


@dataclass
class PathwayResult:
    name: str
    status: str  # met | not_met | missing
    conditions: List[ConditionResult]


@dataclass
class Determination:
    request_id: str
    policy_id: Optional[str]
    policy_title: Optional[str]
    outcome: str  # meets_criteria | missing_information | does_not_meet | no_policy
    summary: str
    pathways: List[PathwayResult] = field(default_factory=list)
    missing_facts: List[str] = field(default_factory=list)
    documentation_checklist: List[str] = field(default_factory=list)
    basis: Optional[str] = None

    def to_dict(self) -> dict:
        return {"request_id": self.request_id, "policy_id": self.policy_id, "outcome": self.outcome,
                "summary": self.summary, "missing_facts": self.missing_facts,
                "documentation_checklist": self.documentation_checklist, "basis": self.basis,
                "pathways": [{"name": p.name, "status": p.status,
                              "conditions": [c.__dict__ for c in p.conditions]} for p in self.pathways]}
