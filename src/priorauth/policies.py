"""Load coverage policies (JSON files in policies/). Policies are data, not code."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

POLICY_DIR = Path(__file__).parent / "policies"
OPS = {">=", "<=", ">", "<", "==", "any_in", "none_in"}


def validate(p: dict) -> dict:
    for key in ("id", "title", "service_codes", "diagnosis_prefixes", "facts", "pathways", "basis"):
        if key not in p:
            raise ValueError(f"policy {p.get('id', '?')}: missing '{key}'")
    for pw in p["pathways"]:
        for c in pw["conditions"]:
            if c["fact"] not in p["facts"]:
                raise ValueError(f"policy {p['id']}: pathway uses undeclared fact '{c['fact']}'")
            if c["op"] not in OPS:
                raise ValueError(f"policy {p['id']}: unknown operator '{c['op']}'")
    return p


def load_policies(directory: Optional[Path] = None) -> List[dict]:
    out: List[dict] = []
    for f in sorted((directory or POLICY_DIR).glob("*.json")):
        data = json.loads(f.read_text())
        out += [validate(p) for p in (data if isinstance(data, list) else [data])]
    ids = [p["id"] for p in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate policy ids")
    return out


def policy_for(service_code: str, policies: List[dict]) -> Optional[dict]:
    for p in policies:
        if service_code in p["service_codes"]:
            return p
    return None


def by_id(policies: List[dict]) -> Dict[str, dict]:
    return {p["id"]: p for p in policies}
