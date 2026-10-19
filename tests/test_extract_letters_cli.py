import json
import subprocess
import sys
from pathlib import Path

import pytest

from priorauth import Request, determine, load_policies
from priorauth.cli import main
from priorauth.extract import extract_llm, extract_rules
from priorauth.letters import missing_info_message, request_letter

ROOT = Path(__file__).resolve().parents[1]
POL = {p["id"]: p for p in load_policies()}


class FakeClient:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user):
        return self.reply


def test_rules_respect_negation_and_do_not_claim_absence():
    facts, _ = extract_rules("No fever. Denies urinary retention. 2 weeks of low back pain.", POL["lumbar-spine-mri"])
    assert "red_flags" not in facts and facts["symptom_duration_weeks"] == 2


def test_llm_extraction_type_and_vocabulary_checks():
    note = "HbA1c 8.1%. On metformin for 9 months. Patient has heart failure."
    reply = json.dumps({
        "hba1c": {"value": 8.1, "evidence": "HbA1c 8.1%"},
        "metformin_trial_months": {"value": "nine", "evidence": "metformin for 9 months"},  # wrong type
        "ascvd_hf_or_ckd": {"value": True, "evidence": "has diabetes and CKD"},  # not in note
    })
    facts, evidence = extract_llm(note, POL["glp1-ra-type2-diabetes"], FakeClient(reply))
    assert facts == {"hba1c": 8.1} and evidence == {"hba1c": "HbA1c 8.1%"}


def test_llm_list_values_restricted_to_policy_vocabulary():
    note = "History of breast cancer. Also has a rash."
    reply = json.dumps({"red_flags": {"value": ["history_of_cancer", "rash"], "evidence": "History of breast cancer"}})
    facts, _ = extract_llm(note, POL["lumbar-spine-mri"], FakeClient(reply))
    assert facts["red_flags"] == ["history_of_cancer"]


def test_letters():
    ok = Request("r1", "72148", ["M54.50"], {"red_flags": ["history_of_cancer"]},
                 evidence={"red_flags": "History of breast cancer"})
    letter = request_letter(ok, determine(ok))
    assert "Red-flag findings" in letter and "History of breast cancer" in letter and "ACR" in letter
    missing = Request("r2", "72148", ["M54.50"], {"symptom_duration_weeks": 8})
    assert "surgery or intervention candidate" in missing_info_message(missing, determine(missing))
    with pytest.raises(ValueError):
        request_letter(missing, determine(missing))


def test_cli(capsys):
    assert main(["check", str(ROOT / "data/requests/lumbar_mri_red_flag.json"), "--letter"]) == 0
    out = capsys.readouterr().out
    assert "MEETS CRITERIA" in out and "Prior authorization request" in out
    assert main(["note", str(ROOT / "data/notes/lumbar_mri_note.txt"), "--service", "72148", "--dx", "M54.42",
                 "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "meets_criteria"


def test_eval_does_not_regress():
    r = subprocess.run([sys.executable, str(ROOT / "eval/run_eval.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
