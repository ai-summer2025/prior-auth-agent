"""Clinical note -> policy facts.

The fact schema comes from the policy itself, so the same extractor works for any
policy file. `extract_rules` handles a few common patterns; `extract_llm` asks a
model for each declared fact with an exact quote. Facts without a verifiable quote,
list values outside the policy's vocabulary, and wrongly typed values are dropped.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Tuple

from .llm import is_grounded, parse_json

KEYWORDS = {
    "suspected_cauda_equina": r"saddle anesthesia|urinary retention|cauda equina|bowel or bladder",
    "progressive_neurologic_deficit": r"progressive (weakness|deficit)|worsening (weakness|foot drop)|foot drop",
    "history_of_cancer": r"history of (\w+ )?(cancer|carcinoma|malignancy)",
    "suspected_infection": r"fever|iv drug use|osteomyelitis|epidural abscess",
    "suspected_fracture": r"compression fracture|osteoporosis with trauma|significant trauma",
    "thunderclap_onset": r"thunderclap|worst headache of (my|her|his) life",
    "focal_neurologic_deficit": r"focal (neurologic )?deficit|hemiparesis|aphasia",
    "papilledema": r"papilledema",
    "immunocompromised": r"\bhiv\b|immunocompromised|transplant",
    "positional_or_valsalva_triggered": r"worse (when )?(lying down|with cough|with valsalva)|valsalva",
    "progressive_or_changed_pattern": r"change in (headache )?pattern|progressively worse",
    "significant_cardiorespiratory_disease": r"heart failure|\bcopd\b",
    "neuromuscular_weakness": r"\bals\b|neuromuscular|myasthenia",
    "awake_hypoventilation": r"hypoventilation",
    "chronic_opioid_use": r"chronic opioid|methadone|oxycodone daily",
    "history_of_stroke": r"history of stroke|prior stroke|\bcva\b",
    "severe_insomnia": r"severe insomnia",
}
NEGATION = re.compile(r"\b(no|denies|without|negative for)\b[^.;\n]{0,40}$", re.I)


def _kw(pattern: str, text: str) -> bool:
    for m in re.finditer(pattern, text, re.I):
        if not NEGATION.search(text[max(0, m.start() - 60):m.start()]):
            return True
    return False


def extract_rules(text: str, policy: dict) -> Tuple[Dict, Dict]:
    facts: Dict = {}
    for name, spec in policy["facts"].items():
        if spec["type"] == "list":
            found = [v for v in spec["values"] if v in KEYWORDS and _kw(KEYWORDS[v], text)]
            if found:  # an empty list would claim "none present"; leave the fact missing instead
                facts[name] = found
    if "symptom_duration_weeks" in policy["facts"]:
        m = re.search(r"(\d+)\s*(week|month)s?\s+(of|history of)\s+(low )?back pain", text, re.I)
        if m:
            facts["symptom_duration_weeks"] = int(m.group(1)) * (4 if m.group(2).lower() == "month" else 1)
    if "conservative_therapy_weeks" in policy["facts"]:
        m = re.search(r"(\d+)\s*weeks? of (physical therapy|pt|conservative)", text, re.I)
        if m:
            facts["conservative_therapy_weeks"] = int(m.group(1))
    if "hba1c" in policy["facts"]:
        m = re.search(r"(?:hba1c|a1c)[^0-9\n]{0,10}(\d+(?:\.\d+)?)", text, re.I)
        if m:
            facts["hba1c"] = float(m.group(1))
    if "metformin_trial_months" in policy["facts"]:
        m = re.search(r"metformin[^.\n]{0,40}?(\d+)\s*months", text, re.I)
        if m:
            facts["metformin_trial_months"] = int(m.group(1))
    if "suspected_osa" in policy["facts"] and _kw(r"sleep apnea|\bosa\b", text):
        facts["suspected_osa"] = True
    return facts, {}


def _typed(spec: dict, value):
    if spec["type"] == "number":
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if spec["type"] == "bool":
        return value if isinstance(value, bool) else None
    if spec["type"] == "list":
        return [v for v in value if v in spec["values"]] if isinstance(value, list) else None
    return None


def extract_llm(text: str, policy: dict, client) -> Tuple[Dict, Dict]:
    schema = {k: {kk: vv for kk, vv in v.items()} for k, v in policy["facts"].items()}
    system = ("You extract facts from a clinical note for a prior authorization review.\n"
              "Fact schema (name -> type, allowed values, meaning):\n" + json.dumps(schema, indent=1) + "\n"
              'Return ONLY a JSON object: {"<fact>": {"value": ..., "evidence": "<exact substring of the note>"}}.\n'
              "For list facts return only allowed values that the note supports; evidence may be one supporting "
              "quote. Omit facts the note does not state. Never infer from absence.")
    data = parse_json(client.complete(system, text), dict)
    facts, evidence = {}, {}
    for name, item in data.items():
        spec = policy["facts"].get(name)
        if not spec or not isinstance(item, dict) or not is_grounded(item.get("evidence"), text):
            continue
        v = _typed(spec, item.get("value"))
        if v is not None:
            facts[name] = v
            evidence[name] = item["evidence"]
    return facts, evidence
