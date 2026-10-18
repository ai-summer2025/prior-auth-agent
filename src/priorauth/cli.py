"""priorauth check request.json [--letter]
priorauth note note.txt --service 72148 --dx M54.50 [--llm] [--letter]
priorauth policies"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .letters import missing_info_message, request_letter
from .pipeline import load_request, request_from_note, run
from .policies import load_policies
from .report import to_markdown


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="priorauth", description="Prior authorization criteria review.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("request")
    n = sub.add_parser("note")
    n.add_argument("note")
    n.add_argument("--service", required=True)
    n.add_argument("--dx", action="append", required=True)
    n.add_argument("--llm", action="store_true")
    for sp in (c, n):
        sp.add_argument("--letter", action="store_true", help="Also print the request letter or missing-info note")
        sp.add_argument("--format", choices=["markdown", "json"], default="markdown")
    sub.add_parser("policies")
    a = p.parse_args(argv)

    if a.cmd == "policies":
        for pol in load_policies():
            print(f"{pol['id']}: {pol['title']} [{', '.join(pol['service_codes'])}]")
        return 0
    req = load_request(a.request) if a.cmd == "check" else request_from_note(
        Path(a.note).read_text(), a.service, a.dx, Path(a.note).stem, use_llm=a.llm)
    d = run(req)
    if a.format == "json":
        print(json.dumps({"facts": req.facts, "evidence": req.evidence, **d.to_dict()}, indent=2))
    else:
        print(to_markdown(req, d))
    if a.letter and a.format == "markdown":
        if d.outcome == "meets_criteria":
            print("\n---\n" + request_letter(req, d))
        elif d.outcome == "missing_information":
            print("\n---\n" + missing_info_message(req, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
