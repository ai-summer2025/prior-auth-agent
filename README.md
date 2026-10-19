# prior-auth-agent

[![CI](https://github.com/ai-summer2025/prior-auth-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ai-summer2025/prior-auth-agent/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Prior authorization criteria review that a utilization-management nurse could audit line by line. Coverage policies are **data files**; a request (structured, or extracted from a clinical note) is evaluated against them with three-valued logic, and the result is one of: **meets criteria**, **missing information** (with exactly what to document), **does not meet** (with the failing criterion), or **no policy**. Request letters and missing-information notes are assembled only from the request's facts and the policy text.

> **Illustrative policies.** The bundled policies are modeled on public clinical guidelines (ACR Appropriateness Criteria, the AASM sleep testing guideline, the ADA Standards of Care). They are not any payer's actual coverage policy. All data is synthetic.

## How it works

```
 clinical note ──► fact extraction ───────────► request ──► policy engine ──────────► determination ──► letter / missing-info note
                   (schema comes from the          ▲         pathways of conditions,   + per-condition    (templated from facts
                    policy; regex or LLM + checks) │         met / not_met / missing    audit trail        and the policy only)
 request JSON ─────────────────────────────────────┘
```

- **Policies as data.** Each policy declares its service codes, covered ICD-10 prefixes, the facts it needs (typed, with allowed values), and one or more *pathways* to approval. Adding a policy means adding a JSON file; the loader validates it.
- **Missing is not negative.** An undocumented fact makes a pathway `missing`, never `not met`. "No comorbidities listed" does not satisfy "no comorbidities".
- **The LLM extracts facts; it does not decide.** Each fact needs an exact quote from the note, must have the declared type, and list values must come from the policy's vocabulary. Anything else is dropped.
- **No approval probabilities.** Payers do not publish them, so the tool does not invent them.

## Bundled policies

| Policy | Service codes | Pathways | Basis |
|---|---|---|---|
| MRI lumbar spine | 72148, 72149, 72158 | red flags; or ≥ 6 weeks of symptoms and of conservative care in a surgical/interventional candidate | ACR Appropriateness Criteria: Low Back Pain |
| MRI brain for headache | 70551-70553 | headache red flags | ACR Appropriateness Criteria: Headache |
| In-lab polysomnography | 95810, 95811 | suspected OSA with a comorbidity that rules out home testing; or negative/inconclusive home test | Kapur et al., *J Clin Sleep Med* 2017 (AASM) |
| Home sleep apnea test | 95806, G0399 | uncomplicated adult with increased risk of moderate-severe OSA | Kapur et al., 2017 (AASM) |
| GLP-1 RA, type 2 diabetes | RX-GLP1RA | HbA1c ≥ 7 after ≥ 3 months of metformin, or with metformin contraindicated; or established ASCVD/HF/CKD | Illustrative step therapy informed by ADA Standards of Care, section 9 |

## Quickstart

```bash
pip install -e .
priorauth policies
priorauth check data/requests/lumbar_mri_red_flag.json --letter
priorauth check data/requests/lumbar_mri_missing_info.json --letter
priorauth note data/notes/lumbar_mri_note.txt --service 72148 --dx M54.42           # regex baseline
priorauth note data/notes/lumbar_mri_note.txt --service 72148 --dx M54.42 --llm     # LLM extraction
docker build -t priorauth . && docker run --rm priorauth
```

Example (abridged):

```
**MISSING INFORMATION** - MRI lumbar spine for low back pain
Cannot determine yet. Provide: surgery_or_intervention_candidate.

**Pathway: Persistent symptoms after conservative care in a surgical or interventional candidate** (missing)
- [x] symptom_duration_weeks >= 6: 10
- [x] conservative_therapy_weeks >= 6: 8
- [?] surgery_or_intervention_candidate == True: not documented
```

LLM configuration: `ANTHROPIC_API_KEY=...`, or `LLM_PROVIDER=openai LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1` for a local model. No SDK dependencies.

## Evaluation

`python eval/run_eval.py` regenerates [`eval/RESULTS.md`](eval/RESULTS.md). CI fails on any engine error or any **unsafe approval** (a note-derived request reaching "meets criteria" when it should not).

**Policy engine:** 14 structured requests covering every outcome and policy, uncovered diagnoses and undocumented facts. All 14 match.

**Note to determination:** 5 synthetic notes with gold facts and outcomes.

| Extractor | Fact accuracy | Outcome accuracy | Unsafe approvals |
|---|---|---|---|
| Regex baseline | 0.73 | 1.00 | 0 |
| LLM + checks | run `python eval/run_eval.py --llm` | | |

The baseline misses facts that need reading comprehension: "considering epidural steroid injection" (an interventional candidate), "new headaches" in a 52-year-old (new headache over 50), and the explicit "no known cardiovascular disease". It got the outcomes right only because other pathways carried them. The LLM row is left for you to run with your own credentials.

## Limitations

- Policies are simplified and illustrative; real payer policies add site-of-service, frequency limits and prior-imaging rules.
- Fact extraction reads a single note; it does not search the full chart.
- Synthetic evaluation only.

## Roadmap

- Emit and consume HL7 Da Vinci formats: Coverage Requirements Discovery (CRD) cards, Documentation Templates and Rules (DTR) questionnaires, and Prior Authorization Support (PAS) bundles, which the CMS Interoperability and Prior Authorization Final Rule (CMS-0057-F) pushes payers toward
- Load policies expressed as FHIR `Questionnaire` + CQL instead of JSON
- Multi-document fact extraction with per-fact provenance

## Project history

Consolidates `prior-auth-agent`, `prior-auth-agent-oct` and `prior-auth-appeal-agent`. The earlier versions attached a "base approval probability" to each policy and adjusted it into a percentage likelihood; those numbers were not derived from data and have been removed. See [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
