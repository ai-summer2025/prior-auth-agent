import pytest

from priorauth import Request, determine, load_policies
from priorauth.engine import evaluate_pathway
from priorauth.policies import validate

POL = load_policies()


def test_all_policies_load_and_validate():
    assert {p["id"] for p in POL} >= {"lumbar-spine-mri", "brain-mri-headache", "in-lab-polysomnography",
                                      "home-sleep-apnea-test", "glp1-ra-type2-diabetes"}


def test_policy_validation_rejects_undeclared_fact():
    bad = {"id": "x", "title": "x", "service_codes": [], "diagnosis_prefixes": [], "basis": "x", "facts": {},
           "pathways": [{"name": "p", "conditions": [{"fact": "nope", "op": "==", "value": 1}]}]}
    with pytest.raises(ValueError):
        validate(bad)


def test_missing_fact_is_never_assumed():
    pw = {"name": "p", "conditions": [{"fact": "a", "op": "none_in", "value": ["x"]}]}
    assert evaluate_pathway(pw, {}).status == "missing"
    assert evaluate_pathway(pw, {"a": []}).status == "met"


def test_not_met_dominates_missing():
    pw = {"name": "p", "conditions": [{"fact": "a", "op": ">=", "value": 6}, {"fact": "b", "op": "==", "value": True}]}
    assert evaluate_pathway(pw, {"a": 2}).status == "not_met"


def test_diagnosis_prefix_check():
    d = determine(Request("r", "72148", ["J45.909"], {"red_flags": ["history_of_cancer"]}), POL)
    assert d.outcome == "does_not_meet" and "not among" in d.summary


def test_missing_information_lists_facts():
    d = determine(Request("r", "72148", ["M54.50"], {"symptom_duration_weeks": 8}), POL)
    assert d.outcome == "missing_information"
    assert "red_flags" in d.missing_facts and "surgery_or_intervention_candidate" in d.missing_facts


def test_unknown_service():
    assert determine(Request("r", "00000", ["Z00.00"]), POL).outcome == "no_policy"
