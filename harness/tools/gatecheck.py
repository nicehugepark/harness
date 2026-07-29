#!/usr/bin/env python3
"""게이트 검사 실행체 — 세션 안의 에이전트가 자기 산출을 자기 승인하지 못하게 한다.

정본: X4 review_gate · C§6.2 의견 스키마 · C§6.3 verify_gate · B§1.3 전이 가드.

**판정은 구조화 채널로만 유효하다.** 세션이 "통과했습니다"라고 적는 것은 판정이
아니고, 이 실행체의 종료 코드가 판정이다. 세션은 자기 산출을 이 게이트에 통과
시켜야만 다음 단계로 갈 수 있다.

  gatecheck.py review   --file v.json [--required N]
  gatecheck.py opinion  --file o.json
  gatecheck.py verify   --file verdict.json --criteria c.json --opinions o.json
  gatecheck.py evidence --file report.json --output-kind code|document|mixed
  gatecheck.py transition --state building --to verifying --request r.json

종료 코드: 0 통과 · 1 불통과 · 2 실행 오류.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import pipeline, policy, schema, workflow  # noqa: E402


def _load(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="게이트 검사 (판정은 종료 코드다)")
    ap.add_argument("kind", choices=["review", "opinion", "verify", "evidence",
                                     "transition"])
    ap.add_argument("--file", required=True)
    ap.add_argument("--criteria")
    ap.add_argument("--opinions")
    ap.add_argument("--required", type=int, default=None)
    ap.add_argument("--output-kind")
    ap.add_argument("--produces-code", default=None)
    ap.add_argument("--state")
    ap.add_argument("--to")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()
    pol = policy.load(pathlib.Path(args.root))

    if args.kind == "review":
        data = _load(args.file)
        verdicts = data if isinstance(data, list) else [data]
        req = args.required or int(pol.get("required_reviewers", 1))
        out = workflow.review_gate(verdicts, required=req)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["result"] == "pass" else 1

    if args.kind == "opinion":
        ok, why = workflow.validate_opinion(_load(args.file))
        print(json.dumps({"ok": ok, "why": why}, ensure_ascii=False))
        return 0 if ok else 1

    if args.kind == "verify":
        ok, why = workflow.verify_gate(_load(args.file),
                                       _load(args.criteria) if args.criteria else [],
                                       _load(args.opinions) if args.opinions else [])
        print(json.dumps({"ok": ok, "why": why}, ensure_ascii=False))
        return 0 if ok else 1

    if args.kind == "evidence":
        rep = _load(args.file)
        pc = None
        if args.produces_code is not None:
            pc = args.produces_code.lower() in ("1", "true", "yes")
        try:
            need = schema.evidence_required(args.output_kind, unit_produces_code=pc)
        except ValueError as exc:
            print(json.dumps({"ok": False, "why": str(exc)}, ensure_ascii=False))
            return 1
        key = schema.EVIDENCE_KEY[need]
        blk = rep.get(key) or {}
        lack = [f for f in schema.EVIDENCE_FIELDS[need]
                if not str(blk.get(f, "")).strip()]
        ok = not lack
        print(json.dumps({"ok": ok, "need": key, "missing": lack},
                         ensure_ascii=False))
        return 0 if ok else 1

    if args.kind == "transition":
        req = _load(args.file)
        req["state"] = args.state or req.get("state")
        ok, why = pipeline.can_transition(req, args.to)
        print(json.dumps({"ok": ok, "why": why}, ensure_ascii=False))
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
