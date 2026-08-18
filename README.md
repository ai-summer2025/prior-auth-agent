# Prior Auth Agent

An autonomous AI agent for healthcare prior authorization workflow automation.
Built to demonstrate how agentic AI can reduce the administrative burden of
prior authorization — one of the top pain points in healthcare operations.

> **Note:** All healthcare organization names, EHR system names, and payer names
> are intentionally omitted or genericized. This tool is a reference implementation.

---

## Problem Statement

Prior authorization (PA) requires clinicians to submit clinical justification to
payers before certain procedures are approved. Studies estimate:

- **93% of physicians** report PA causes care delays (AMA, 2023)
- Practices spend **~13 hours per week per physician** on PA
- Administrative costs from PA exceed **$11 billion annually**

This agent automates the evaluation, documentation, and appeal drafting steps
of the PA lifecycle.

---

## Features

- **State machine workflow**: PENDING → REVIEWING → APPROVED/DENIED → APPEALING → RESOLVED
- **Criteria evaluation engine**: Maps CPT procedure codes to payer-agnostic clinical criteria
- **Automated appeal drafting**: Generates structured appeal letters for denied requests
- **Persistent state store**: JSON-backed storage for request tracking across sessions
- **Dashboard reporting**: Summary view of all authorization requests by status
- **Zero external dependencies**: Pure Python standard library (3.9+)

---

## Supported Procedure Codes

| CPT Code | Procedure | Key Criteria |
|----------|-----------|--------------|
| 72148 | MRI Lumbar Spine | 6-week conservative treatment, appropriate diagnosis |
| 27447 | Total Knee Arthroplasty | Failed conservative care 6mo+, radiographic evidence, BMI documented |
| 95251 | Continuous Glucose Monitor | Diabetes diagnosis, insulin use or hypoglycemia risk |
| 94625 | Pulmonary Rehabilitation | COPD/pulmonary diagnosis, physician referral, functional limitation |
| (any) | Default policy | Clinical narrative present, diagnosis code present |

---

## Workflow

```
Submit Note → Validate → PENDING
                              ↓
                         Evaluate Criteria → REVIEWING
                              ↓
               ┌─────────────┴─────────────┐
            APPROVED                    DENIED
               ↓                           ↓
           Resolve                  Draft Appeal → APPEALING
                                          ↓
                                       Resolve
```

---

## Quick Start

```bash
python prior_auth_agent.py
```

This runs four demonstration scenarios through the full workflow and prints a dashboard.

---

## Running Tests

```bash
python -m unittest test_prior_auth_agent -v
```

All 32+ test cases should pass with exit code 0.

---

## Architecture

### Core Classes

**`ClinicalNote`** — Structured clinical input (patient demographics, procedure, narrative, diagnosis codes, prior treatments). Validates itself and returns a list of errors.

**`CriteriaEvaluator`** — Maps procedure codes to payer criteria. Each criterion is a named check method returning `(met: bool, evidence: str)`. Produces an `EvaluationResult` with per-criterion outcomes.

**`AppealDrafter`** — Generates a structured appeal letter from failed criteria and clinical context. Raises `ValueError` if the request was approved (no appeal needed).

**`StateStore`** — Persists `AuthRequest` objects to a JSON file. Supports get, put, all, and by-status queries.

**`PriorAuthAgent`** — Orchestrates the full lifecycle: submit, evaluate, appeal, resolve. Exposes `run_full_workflow()` for single-call end-to-end execution.

### State Machine

```python
TRANSITIONS = {
    PENDING:   [REVIEWING],
    REVIEWING: [APPROVED, DENIED],
    APPROVED:  [RESOLVED],
    DENIED:    [APPEALING, RESOLVED],
    APPEALING: [RESOLVED],
    RESOLVED:  [],
}
```

Invalid transitions raise `ValueError`.

---

## Sample Output

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PRIOR AUTHORIZATION DASHBOARD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 APPROVED : 2
 APPEALING: 1
 RESOLVED : 1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Design Decisions

- **No external APIs**: All payer criteria are encoded in `PAYER_POLICIES`. Real deployments would call payer APIs.
- **Mock criteria**: Criteria reflect publicly documented CMS/AMA clinical guidelines without reproducing proprietary payer rules.
- **JSON persistence**: Simple file-backed store. Production would use a database.
- **Standard library only**: No pip dependencies for maximum portability.

---

## License

MIT
