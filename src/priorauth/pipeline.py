from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .engine import determine
from .extract import extract_llm, extract_rules
from .models import Determination, Request
from .policies import load_policies, policy_for


def request_from_dict(d: dict) -> Request:
    return Request(**{k: v for k, v in d.items() if k in Request.__dataclass_fields__})


def load_request(path: str) -> Request:
    return request_from_dict(json.loads(Path(path).read_text()))


def request_from_note(text: str, service_code: str, diagnosis_codes: List[str], request_id: str = "note",
                      use_llm: bool = False, client=None, policies: Optional[list] = None) -> Request:
    policies = policies if policies is not None else load_policies()
    policy = policy_for(service_code, policies)
    facts, evidence = {}, {}
    if policy is not None:
        if use_llm:
            if client is None:
                from .llm import client_from_env
                client = client_from_env()
            facts, evidence = extract_llm(text, policy, client)
        else:
            facts, evidence = extract_rules(text, policy)
    return Request(request_id, service_code, diagnosis_codes, facts, evidence=evidence)


def run(req: Request) -> Determination:
    return determine(req)
