# Changelog

## 1.0.0

Consolidates three earlier prototypes into the `priorauth` package.

- Coverage policies as validated JSON data (service codes, ICD-10 prefixes, typed facts, approval pathways)
- Five illustrative policies grounded in public guidelines (ACR, AASM, ADA), each naming its basis
- Three-valued engine: undocumented facts are "missing", never assumed negative
- Fact extraction whose schema comes from the policy: regex baseline and an LLM extractor with quote,
  type and vocabulary checks
- Templated request letters, missing-information notes and appeal points built only from facts and policy text
- Removed invented "approval probabilities"
- Evaluation: 14 engine cases and 5 labeled notes; unsafe approvals fail CI
- GitHub Actions on Python 3.9-3.13, ruff, Dockerfile
