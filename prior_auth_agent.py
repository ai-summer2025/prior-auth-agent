#!/usr/bin/env python3
"""
Prior Authorization Agentic Workflow System

Autonomous multi-step agent that:
  1. Ingests structured clinical notes (patient, diagnosis, procedure)
  2. Evaluates payer criteria through a reasoning loop
  3. Tracks each request through a state machine
  4. Drafts denial appeal letters with clinical justification
  5. Persists state to JSON for cross-session tracking

Architecture: Rule-based criteria engine (no paid APIs required).
All payer policies are embedded as mock data reflecting real-world
prior auth criteria patterns documented in CMS/AMA literature.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
import textwrap
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STATE_FILE = Path("/tmp/prior_auth_agent_state.json")

# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------

class AuthStatus(str, Enum):
    PENDING    = "PENDING"      # Submitted, awaiting evaluation
    REVIEWING  = "REVIEWING"    # Agent is actively evaluating criteria
    APPROVED   = "APPROVED"     # All criteria met
    DENIED     = "DENIED"       # One or more criteria failed
    APPEALING  = "APPEALING"    # Denial appealed with clinical justification
    RESOLVED   = "RESOLVED"     # Appeal accepted or upheld

# Valid state transitions
TRANSITIONS: dict[AuthStatus, list[AuthStatus]] = {
    AuthStatus.PENDING:   [AuthStatus.REVIEWING],
    AuthStatus.REVIEWING: [AuthStatus.APPROVED, AuthStatus.DENIED],
    AuthStatus.APPROVED:  [AuthStatus.RESOLVED],
    AuthStatus.DENIED:    [AuthStatus.APPEALING, AuthStatus.RESOLVED],
    AuthStatus.APPEALING: [AuthStatus.RESOLVED],
    AuthStatus.RESOLVED:  [],
}


def validate_transition(current: AuthStatus, next_state: AuthStatus) -> None:
    if next_state not in TRANSITIONS[current]:
        raise ValueError(
            f"Invalid transition: {current.value} → {next_state.value}. "
            f"Allowed: {[s.value for s in TRANSITIONS[current]]}"
        )


# ---------------------------------------------------------------------------
# Clinical Data Structures
# ---------------------------------------------------------------------------

@dataclass
class ClinicalNote:
    """Structured clinical note submitted with a prior auth request."""
    patient_id:          str
    patient_age:         int
    diagnosis_codes:     list[str]   # ICD-10 codes
    procedure_code:      str         # CPT code
    procedure_name:      str
    clinical_narrative:  str
    ordering_provider:   str
    prior_treatments:    list[str]   # Conservative treatments already tried
    urgency:             str         # "routine" | "urgent" | "emergent"
    request_date:        str = field(default_factory=lambda: _now())

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors: list[str] = []
        if self.patient_age < 0 or self.patient_age > 150:
            errors.append(f"Implausible patient age: {self.patient_age}")
        if not self.diagnosis_codes:
            errors.append("At least one diagnosis code required")
        if not re.match(r"^\d{4,5}[A-Z]?$", self.procedure_code):
            errors.append(f"Procedure code format invalid: {self.procedure_code}")
        if not self.clinical_narrative.strip():
            errors.append("Clinical narrative cannot be empty")
        if self.urgency not in ("routine", "urgent", "emergent"):
            errors.append(f"Urgency must be routine/urgent/emergent, got: {self.urgency}")
        return errors


@dataclass
class CriterionResult:
    """Outcome of evaluating a single coverage criterion."""
    criterion_name: str
    met:            bool
    evidence:       str   # What in the note supports/fails this criterion
    required:       bool  # True = blocking (must be met for approval)


@dataclass
class EvaluationResult:
    """Aggregate outcome of the criteria evaluation loop."""
    request_id:       str
    procedure_code:   str
    criteria_results: list[CriterionResult]
    recommended_status: AuthStatus
    summary:          str
    evaluated_at:     str = field(default_factory=lambda: _now())

    @property
    def failed_required(self) -> list[CriterionResult]:
        return [c for c in self.criteria_results if c.required and not c.met]

    @property
    def all_required_met(self) -> bool:
        return len(self.failed_required) == 0


@dataclass
class AuthRequest:
    """Full lifecycle record for a prior authorization request."""
    request_id:          str
    note:                ClinicalNote
    status:              AuthStatus
    evaluation:          Optional[EvaluationResult]
    appeal_letter:       Optional[str]
    appeal_submitted_at: Optional[str]
    resolution_note:     Optional[str]
    history:             list[dict]   # Audit trail of status changes
    created_at:          str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        if self.evaluation:
            d["evaluation"]["recommended_status"] = (
                self.evaluation.recommended_status.value
            )
        return d

    @staticmethod
    def from_dict(d: dict) -> "AuthRequest":
        d = dict(d)
        d["status"] = AuthStatus(d["status"])
        note_d = d["note"]
        d["note"] = ClinicalNote(**note_d)
        if d.get("evaluation"):
            ev = d["evaluation"]
            ev["recommended_status"] = AuthStatus(ev["recommended_status"])
            ev["criteria_results"] = [
                CriterionResult(**c) for c in ev["criteria_results"]
            ]
            d["evaluation"] = EvaluationResult(**ev)
        return AuthRequest(**d)


# ---------------------------------------------------------------------------
# Mock Payer Policy Engine
# ---------------------------------------------------------------------------

@dataclass
class PayerCriterion:
    name:     str
    required: bool
    check:    str   # Name of the check method to call


# Payer policy library: CPT → list of criteria
# Based on documented commercial payer prior auth requirement patterns
PAYER_POLICIES: dict[str, list[PayerCriterion]] = {
    # MRI Lumbar Spine
    "72148": [
        PayerCriterion("conservative_treatment_6_weeks", required=True,
                       check="check_conservative_treatment"),
        PayerCriterion("appropriate_diagnosis", required=True,
                       check="check_lumbar_diagnosis"),
        PayerCriterion("no_recent_duplicate_imaging", required=True,
                       check="check_no_duplicate_imaging"),
        PayerCriterion("provider_is_ordering_clinician", required=False,
                       check="check_provider_documented"),
    ],
    # Total Knee Arthroplasty
    "27447": [
        PayerCriterion("failed_conservative_6_months", required=True,
                       check="check_conservative_treatment"),
        PayerCriterion("radiographic_evidence_documented", required=True,
                       check="check_imaging_evidence"),
        PayerCriterion("appropriate_knee_diagnosis", required=True,
                       check="check_knee_diagnosis"),
        PayerCriterion("bmi_documented", required=False,
                       check="check_bmi_documented"),
    ],
    # Continuous Glucose Monitor Supply
    "95251": [
        PayerCriterion("diabetes_diagnosis_required", required=True,
                       check="check_diabetes_diagnosis"),
        PayerCriterion("insulin_use_or_hypoglycemia_risk", required=True,
                       check="check_insulin_or_hypo_risk"),
        PayerCriterion("provider_training_documented", required=False,
                       check="check_provider_documented"),
    ],
    # Pulmonary Rehabilitation
    "94625": [
        PayerCriterion("copd_or_pulmonary_diagnosis", required=True,
                       check="check_pulmonary_diagnosis"),
        PayerCriterion("physician_referral_documented", required=True,
                       check="check_provider_documented"),
        PayerCriterion("functional_limitation_documented", required=False,
                       check="check_functional_limitation"),
    ],
    # Default policy for unknown CPT codes
    "__default__": [
        PayerCriterion("clinical_narrative_present", required=True,
                       check="check_narrative_length"),
        PayerCriterion("diagnosis_code_present", required=True,
                       check="check_diagnosis_present"),
    ],
}


class CriteriaEvaluator:
    """
    Multi-step agent loop that evaluates payer criteria against a clinical note.

    Each check_ method returns (met: bool, evidence: str).
    The agent loops through all criteria for the procedure code and
    accumulates results before emitting a final recommendation.
    """

    # ICD-10 prefixes for various condition categories
    LUMBAR_DX   = {"M47", "M51", "M54", "G54", "S33", "M43"}
    KNEE_DX     = {"M17", "M23", "M25", "S82", "S83", "M79"}
    DIABETES_DX = {"E10", "E11", "E13"}
    PULM_DX     = {"J44", "J45", "J47", "J96", "J98"}

    def evaluate(self, request_id: str, note: ClinicalNote) -> EvaluationResult:
        policy = PAYER_POLICIES.get(note.procedure_code,
                                    PAYER_POLICIES["__default__"])
        results: list[CriterionResult] = []

        for criterion in policy:
            check_fn = getattr(self, criterion.check, None)
            if check_fn is None:
                # Fallback: mark as met if no check implemented
                met, evidence = True, "No automated check — assumed met"
            else:
                met, evidence = check_fn(note)

            results.append(CriterionResult(
                criterion_name=criterion.name,
                met=met,
                evidence=evidence,
                required=criterion.required,
            ))

        # Build aggregate result
        ev = EvaluationResult(
            request_id=request_id,
            procedure_code=note.procedure_code,
            criteria_results=results,
            recommended_status=(
                AuthStatus.APPROVED if all(c.met for c in results if c.required)
                else AuthStatus.DENIED
            ),
            summary=self._build_summary(results),
        )
        return ev

    # ------------------------------------------------------------------
    # Individual criterion check methods
    # ------------------------------------------------------------------

    def check_conservative_treatment(self, note: ClinicalNote):
        keywords = {"physical therapy", "pt", "nsaid", "conservative",
                    "ibuprofen", "naproxen", "chiropractic", "steroid injection",
                    "weight loss", "exercise", "activity modification"}
        narrative_lower = note.clinical_narrative.lower()
        prior_lower     = " ".join(note.prior_treatments).lower()
        combined        = narrative_lower + " " + prior_lower

        found = [kw for kw in keywords if kw in combined]
        if found or len(note.prior_treatments) >= 1:
            return True, f"Conservative treatment documented: {', '.join(found) if found else note.prior_treatments}"
        return False, "No conservative treatment documented in narrative or prior_treatments field"

    def check_lumbar_diagnosis(self, note: ClinicalNote):
        for code in note.diagnosis_codes:
            if any(code.startswith(p) for p in self.LUMBAR_DX):
                return True, f"Lumbar/spinal diagnosis code found: {code}"
        return False, f"No lumbar diagnosis code. Provided: {note.diagnosis_codes}"

    def check_knee_diagnosis(self, note: ClinicalNote):
        for code in note.diagnosis_codes:
            if any(code.startswith(p) for p in self.KNEE_DX):
                return True, f"Knee diagnosis code found: {code}"
        return False, f"No knee diagnosis code. Provided: {note.diagnosis_codes}"

    def check_diabetes_diagnosis(self, note: ClinicalNote):
        for code in note.diagnosis_codes:
            if any(code.startswith(p) for p in self.DIABETES_DX):
                return True, f"Diabetes diagnosis confirmed: {code}"
        return False, f"No diabetes diagnosis code found. Codes: {note.diagnosis_codes}"

    def check_insulin_or_hypo_risk(self, note: ClinicalNote):
        keywords = {"insulin", "hypoglycemia", "hypoglycemic", "basal",
                    "bolus", "a1c", "glucose instability", "labile"}
        combined = (note.clinical_narrative + " " +
                    " ".join(note.prior_treatments)).lower()
        found = [kw for kw in keywords if kw in combined]
        if found:
            return True, f"Insulin use/hypoglycemia risk documented: {', '.join(found)}"
        return False, "No insulin use or hypoglycemia risk documented"

    def check_pulmonary_diagnosis(self, note: ClinicalNote):
        for code in note.diagnosis_codes:
            if any(code.startswith(p) for p in self.PULM_DX):
                return True, f"Pulmonary diagnosis code found: {code}"
        return False, f"No pulmonary diagnosis. Provided: {note.diagnosis_codes}"

    def check_imaging_evidence(self, note: ClinicalNote):
        keywords = {"x-ray", "xray", "radiograph", "mri", "ct scan",
                    "imaging", "film", "arthritis", "joint space narrowing",
                    "osteophyte", "bone-on-bone"}
        combined = note.clinical_narrative.lower()
        found = [kw for kw in keywords if kw in combined]
        if found:
            return True, f"Imaging/radiographic evidence referenced: {', '.join(found)}"
        return False, "No radiographic or imaging evidence documented in narrative"

    def check_no_duplicate_imaging(self, note: ClinicalNote):
        keywords = {"repeat", "duplicate", "same site", "recent mri",
                    "already had mri", "prior mri"}
        combined = note.clinical_narrative.lower()
        found = [kw for kw in keywords if kw in combined]
        if found:
            return False, f"Potential duplicate imaging flagged: {', '.join(found)}"
        return True, "No evidence of recent duplicate imaging in narrative"

    def check_provider_documented(self, note: ClinicalNote):
        if note.ordering_provider and len(note.ordering_provider.strip()) > 3:
            return True, f"Ordering provider documented: {note.ordering_provider}"
        return False, "Ordering provider name missing or too short"

    def check_narrative_length(self, note: ClinicalNote):
        word_count = len(note.clinical_narrative.split())
        if word_count >= 20:
            return True, f"Narrative has {word_count} words (minimum 20)"
        return False, f"Narrative too brief: {word_count} words (minimum 20 required)"

    def check_diagnosis_present(self, note: ClinicalNote):
        if note.diagnosis_codes:
            return True, f"Diagnosis code(s) present: {', '.join(note.diagnosis_codes)}"
        return False, "No diagnosis codes provided"

    def check_bmi_documented(self, note: ClinicalNote):
        if re.search(r"bmi\s*[\:\=]?\s*\d{2}", note.clinical_narrative, re.I):
            return True, "BMI value found in clinical narrative"
        return False, "BMI not explicitly documented in narrative (non-required)"

    def check_functional_limitation(self, note: ClinicalNote):
        keywords = {"dyspnea", "shortness of breath", "sob", "limitation",
                    "exertion", "six-minute walk", "6mwt", "mrc scale",
                    "functional decline"}
        combined = note.clinical_narrative.lower()
        found = [kw for kw in keywords if kw in combined]
        if found:
            return True, f"Functional limitation documented: {', '.join(found)}"
        return False, "Functional limitation not explicitly documented"

    # ------------------------------------------------------------------
    # Summary helper
    # ------------------------------------------------------------------

    def _build_summary(self, results: list[CriterionResult]) -> str:
        failed_req  = [c for c in results if c.required and not c.met]
        failed_opt  = [c for c in results if not c.required and not c.met]
        passed      = [c for c in results if c.met]

        lines = [
            f"Criteria evaluated: {len(results)}",
            f"  Passed:  {len(passed)}",
            f"  Failed (required):  {len(failed_req)}",
            f"  Failed (optional):  {len(failed_opt)}",
        ]
        if failed_req:
            lines.append("Blocking failures:")
            for c in failed_req:
                lines.append(f"  ✗ {c.criterion_name}: {c.evidence}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Appeal Letter Drafter
# ---------------------------------------------------------------------------

class AppealDrafter:
    """
    Generates a structured denial appeal letter incorporating:
    - Failed criteria with clinical justification
    - Prior treatment history
    - Clinical narrative excerpts
    - Request for peer-to-peer review
    """

    TEMPLATE = textwrap.dedent("""\
        PRIOR AUTHORIZATION APPEAL LETTER
        ==========================================================
        Date:              {date}
        Request ID:        {request_id}
        Patient ID:        {patient_id}
        Procedure:         {procedure_name} (CPT {procedure_code})
        Ordering Provider: {ordering_provider}
        Urgency:           {urgency}

        TO WHOM IT MAY CONCERN:

        We are writing to formally appeal the denial of the prior authorization
        request for {procedure_name} (CPT {procedure_code}) for patient
        {patient_id}, a {patient_age}-year-old individual.

        CLINICAL JUSTIFICATION
        ----------------------------------------------------------
        {clinical_narrative}

        PRIOR CONSERVATIVE TREATMENTS ATTEMPTED
        ----------------------------------------------------------
        {prior_treatments}

        RESPONSE TO DENIAL CRITERIA
        ----------------------------------------------------------
        {criteria_responses}

        SUPPORTING DIAGNOSIS CODES
        ----------------------------------------------------------
        {diagnosis_codes}

        MEDICAL NECESSITY STATEMENT
        ----------------------------------------------------------
        The requested procedure is medically necessary for this patient based
        on the clinical documentation provided above. Delaying or denying this
        service may result in significant harm, including disease progression,
        increased pain and functional impairment, and potential adverse outcomes.

        We respectfully request:
        1. Reconsideration of this denial based on the clinical evidence above.
        2. If additional documentation is required, please specify in writing.
        3. A peer-to-peer review with the ordering provider within 72 hours.

        Sincerely,
        {ordering_provider}
        Date: {date}
        ==========================================================
        """)

    def draft(
        self,
        request_id: str,
        note: ClinicalNote,
        evaluation: EvaluationResult,
    ) -> str:
        if evaluation.recommended_status != AuthStatus.DENIED:
            raise ValueError("Appeal letters are only generated for denied requests")

        criteria_responses = self._build_criteria_responses(
            evaluation.failed_required
        )

        prior_tx = (
            "\n".join(f"  • {tx}" for tx in note.prior_treatments)
            if note.prior_treatments
            else "  • Patient has undergone appropriate workup per clinical guidelines"
        )

        return self.TEMPLATE.format(
            date=_now()[:10],
            request_id=request_id,
            patient_id=note.patient_id,
            procedure_name=note.procedure_name,
            procedure_code=note.procedure_code,
            ordering_provider=note.ordering_provider,
            urgency=note.urgency.upper(),
            clinical_narrative=textwrap.fill(note.clinical_narrative, width=70,
                                             initial_indent="  ",
                                             subsequent_indent="  "),
            prior_treatments=prior_tx,
            criteria_responses=criteria_responses,
            diagnosis_codes=", ".join(note.diagnosis_codes),
            patient_age=note.patient_age,
        )

    def _build_criteria_responses(self, failed: list[CriterionResult]) -> str:
        if not failed:
            return "  All required criteria were satisfied."
        lines = []
        for i, c in enumerate(failed, 1):
            lines.append(f"  Criterion {i}: {c.criterion_name}")
            lines.append(f"    Denial basis: {c.evidence}")
            lines.append(
                f"    Clinical response: The clinical documentation clearly "
                f"supports medical necessity for this criterion. Please refer "
                f"to the clinical narrative and supporting records attached."
            )
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# State Persistence
# ---------------------------------------------------------------------------

class StateStore:
    """JSON-backed persistence for auth requests."""

    def __init__(self, path: Path = DEFAULT_STATE_FILE):
        self.path = path
        self._requests: dict[str, AuthRequest] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                for rid, d in raw.items():
                    self._requests[rid] = AuthRequest.from_dict(d)
            except (json.JSONDecodeError, KeyError):
                self._requests = {}

    def save(self) -> None:
        data = {rid: req.to_dict() for rid, req in self._requests.items()}
        self.path.write_text(json.dumps(data, indent=2))

    def put(self, req: AuthRequest) -> None:
        self._requests[req.request_id] = req
        self.save()

    def get(self, request_id: str) -> Optional[AuthRequest]:
        return self._requests.get(request_id)

    def all(self) -> list[AuthRequest]:
        return list(self._requests.values())

    def by_status(self, status: AuthStatus) -> list[AuthRequest]:
        return [r for r in self._requests.values() if r.status == status]


# ---------------------------------------------------------------------------
# Main Agent
# ---------------------------------------------------------------------------

class PriorAuthAgent:
    """
    Orchestrator: submits requests, runs evaluation loop, manages transitions,
    triggers appeal drafting, and tracks resolution.
    """

    def __init__(self, store: Optional[StateStore] = None,
                 verbose: bool = True):
        self.store     = store or StateStore()
        self.evaluator = CriteriaEvaluator()
        self.drafter   = AppealDrafter()
        self.verbose   = verbose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, note: ClinicalNote) -> AuthRequest:
        """STEP 1: Validate and submit a new prior auth request."""
        errors = note.validate()
        if errors:
            raise ValueError(f"Clinical note validation failed: {errors}")

        request_id = str(uuid.uuid4())[:8].upper()
        req = AuthRequest(
            request_id=request_id,
            note=note,
            status=AuthStatus.PENDING,
            evaluation=None,
            appeal_letter=None,
            appeal_submitted_at=None,
            resolution_note=None,
            history=[_audit_entry(None, AuthStatus.PENDING, "Request submitted")],
            created_at=_now(),
        )
        self.store.put(req)
        self._log(f"[SUBMIT] Request {request_id} created → PENDING")
        return req

    def run_evaluation(self, request_id: str) -> AuthRequest:
        """STEP 2–3: Transition to REVIEWING, evaluate criteria, emit APPROVED/DENIED."""
        req = self._get_or_raise(request_id)
        req = self._transition(req, AuthStatus.REVIEWING, "Agent started evaluation")

        self._log(f"[EVAL]   Evaluating {len(PAYER_POLICIES.get(req.note.procedure_code, PAYER_POLICIES['__default__']))} criteria for CPT {req.note.procedure_code}...")

        evaluation = self.evaluator.evaluate(request_id, req.note)
        req.evaluation = evaluation

        next_status = evaluation.recommended_status
        reason = (
            f"All required criteria met"
            if next_status == AuthStatus.APPROVED
            else f"Failed {len(evaluation.failed_required)} required criterion(a)"
        )
        req = self._transition(req, next_status, reason)
        self._log(f"[EVAL]   Result: {next_status.value}")
        if self.verbose and evaluation.failed_required:
            for c in evaluation.failed_required:
                self._log(f"         ✗ {c.criterion_name}: {c.evidence}")
        return req

    def submit_appeal(self, request_id: str) -> AuthRequest:
        """STEP 4: Draft appeal letter and transition to APPEALING."""
        req = self._get_or_raise(request_id)
        if req.evaluation is None:
            raise ValueError("Cannot appeal before evaluation is run")

        letter = self.drafter.draft(request_id, req.note, req.evaluation)
        req.appeal_letter = letter
        req.appeal_submitted_at = _now()

        req = self._transition(req, AuthStatus.APPEALING, "Appeal letter drafted and submitted")
        self._log(f"[APPEAL] Appeal submitted for {request_id}")
        return req

    def resolve(self, request_id: str, resolution: str) -> AuthRequest:
        """STEP 5: Mark a request as resolved (approved, upheld denial, etc.)."""
        req = self._get_or_raise(request_id)
        req.resolution_note = resolution
        req = self._transition(req, AuthStatus.RESOLVED, f"Resolved: {resolution}")
        self._log(f"[RESOLVE] {request_id} resolved: {resolution}")
        return req

    def run_full_workflow(self, note: ClinicalNote) -> AuthRequest:
        """
        Complete agentic loop for a single clinical note:
        Submit → Review → Approve/Deny → (if denied) Appeal
        """
        req = self.submit(note)
        req = self.run_evaluation(req.request_id)

        if req.status == AuthStatus.DENIED:
            self._log(f"[AGENT]  Denial detected — auto-generating appeal letter")
            req = self.submit_appeal(req.request_id)

        return req

    def get_status_report(self, request_id: str) -> str:
        """Human-readable status report for a request."""
        req = self._get_or_raise(request_id)
        lines = [
            f"Request ID:   {req.request_id}",
            f"Patient:      {req.note.patient_id} (age {req.note.patient_age})",
            f"Procedure:    {req.note.procedure_name} (CPT {req.note.procedure_code})",
            f"Status:       {req.status.value}",
            f"Submitted:    {req.created_at}",
        ]
        if req.evaluation:
            lines.append(f"\nEvaluation Summary:\n{req.evaluation.summary}")
        if req.appeal_letter:
            lines.append(f"\nAppeal submitted at: {req.appeal_submitted_at}")
        if req.resolution_note:
            lines.append(f"Resolution: {req.resolution_note}")
        lines.append(f"\nHistory:")
        for h in req.history:
            lines.append(f"  {h['timestamp'][:19]}  {h['from'] or '—':10s} → {h['to']:10s}  {h['reason']}")
        return "\n".join(lines)

    def dashboard(self) -> str:
        """Summary of all requests by status."""
        all_reqs = self.store.all()
        if not all_reqs:
            return "No prior authorization requests on file."

        counts = {}
        for s in AuthStatus:
            counts[s] = len([r for r in all_reqs if r.status == s])

        lines = ["=== Prior Auth Dashboard ==="]
        for s, n in counts.items():
            if n > 0:
                lines.append(f"  {s.value:12s}: {n}")
        lines.append(f"  {'TOTAL':12s}: {len(all_reqs)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_raise(self, request_id: str) -> AuthRequest:
        req = self.store.get(request_id)
        if req is None:
            raise KeyError(f"Request not found: {request_id}")
        return req

    def _transition(self, req: AuthRequest, to: AuthStatus, reason: str) -> AuthRequest:
        validate_transition(req.status, to)
        from_status = req.status
        req.status = to
        req.history.append(_audit_entry(from_status, to, reason))
        self.store.put(req)
        return req

    def _log(self, msg: str) -> None:
        if self.verbose:
            ts = _now()[:19]
            print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_entry(
    from_status: Optional[AuthStatus],
    to: AuthStatus,
    reason: str,
) -> dict:
    return {
        "timestamp": _now(),
        "from": from_status.value if from_status else None,
        "to": to.value,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Sample data factory
# ---------------------------------------------------------------------------

def make_sample_note(scenario: str = "mri_approved") -> ClinicalNote:
    """Return a pre-built clinical note for testing/demo purposes."""
    samples = {
        "mri_approved": ClinicalNote(
            patient_id="PT-001",
            patient_age=52,
            diagnosis_codes=["M54.5", "G54.4"],
            procedure_code="72148",
            procedure_name="MRI Lumbar Spine without contrast",
            clinical_narrative=(
                "Patient presents with six weeks of low back pain radiating to "
                "the left leg consistent with L4-L5 radiculopathy. Physical therapy "
                "for 8 weeks and NSAID therapy (naproxen 500mg BID) have failed to "
                "provide adequate relief. Neurologic exam reveals mild L4 dermatomal "
                "hypoesthesia. MRI is requested to evaluate for disc herniation or "
                "spinal stenosis prior to consideration of epidural steroid injection."
            ),
            ordering_provider="Dr. Jane Smith, MD",
            prior_treatments=["Physical therapy x8 weeks", "Naproxen 500mg BID x6 weeks"],
            urgency="routine",
        ),
        "mri_denied": ClinicalNote(
            patient_id="PT-002",
            patient_age=35,
            diagnosis_codes=["M54.5"],
            procedure_code="72148",
            procedure_name="MRI Lumbar Spine without contrast",
            clinical_narrative=(
                "Patient has back pain and requests MRI scan."
            ),
            ordering_provider="Dr. Bob Jones",
            prior_treatments=[],
            urgency="routine",
        ),
        "knee_approved": ClinicalNote(
            patient_id="PT-003",
            patient_age=67,
            diagnosis_codes=["M17.11"],
            procedure_code="27447",
            procedure_name="Total Knee Arthroplasty",
            clinical_narrative=(
                "Patient is a 67-year-old with severe right knee osteoarthritis "
                "confirmed on weight-bearing X-ray showing bone-on-bone joint space "
                "narrowing and osteophyte formation. Conservative management including "
                "physical therapy, NSAIDs, and two corticosteroid injections over 18 "
                "months have failed. BMI: 28. Functional status significantly impaired. "
                "Patient is a good surgical candidate after cardiac clearance."
            ),
            ordering_provider="Dr. Sarah Lee, MD, Orthopedic Surgery",
            prior_treatments=[
                "Physical therapy x6 months",
                "Naproxen 500mg BID",
                "Corticosteroid injection x2",
                "Activity modification",
            ],
            urgency="routine",
        ),
        "cgm_approved": ClinicalNote(
            patient_id="PT-004",
            patient_age=44,
            diagnosis_codes=["E11.641"],
            procedure_code="95251",
            procedure_name="Continuous Glucose Monitor (CGM) Training and Supply",
            clinical_narrative=(
                "Patient has Type 2 diabetes mellitus on basal-bolus insulin therapy. "
                "A1c is 9.2%, indicating poorly controlled glucose. Patient has had "
                "three hypoglycemia events in the past 60 days requiring intervention. "
                "CGM is requested to improve glucose stability and reduce hypoglycemia "
                "episodes. Provider has completed CGM training program."
            ),
            ordering_provider="Dr. Maria Chen, MD, Endocrinology",
            prior_treatments=["Metformin", "Basal-bolus insulin (glargine + lispro)"],
            urgency="routine",
        ),
    }
    note = samples.get(scenario)
    if note is None:
        raise ValueError(f"Unknown scenario: {scenario}. Choose from: {list(samples)}")
    return note


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _print_banner():
    print("=" * 60)
    print("  Prior Authorization Agentic Workflow System")
    print("  Healthcare AI — Autonomous Criteria Evaluation")
    print("=" * 60)


def cli_main():
    _print_banner()
    agent = PriorAuthAgent(verbose=True)

    scenarios = ["mri_approved", "mri_denied", "knee_approved", "cgm_approved"]
    processed = []

    for scenario in scenarios:
        print(f"\n{'─'*60}")
        print(f"SCENARIO: {scenario}")
        print('─'*60)
        note = make_sample_note(scenario)
        req = agent.run_full_workflow(note)
        processed.append(req)
        print()
        print(agent.get_status_report(req.request_id))

        if req.appeal_letter:
            print("\n[APPEAL LETTER PREVIEW — first 20 lines]")
            for line in req.appeal_letter.splitlines()[:20]:
                print(line)
            print("  ...")

    print(f"\n{'─'*60}")
    print(agent.dashboard())
    return processed


if __name__ == "__main__":
    cli_main()
