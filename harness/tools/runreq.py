#!/usr/bin/env python3
"""요청 1건 완주 실행체 — 워크플로 엔진의 실물 executor.

정본: B§2.2 골격 · B§2.4 스폰 파라미터 규율 · C§2.2 투입 매트릭스.

executor 는 역할별 헤드리스 세션을 띄우고 **구조화 출력**을 받는다.
스폰 입력은 파라미터로만 전달한다 — 완료기준은 원장 경로(포인터), 기준선은
ref 이름이며 프롬프트 본문에 기준 재기술·해시 리터럴을 넣지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "harness" / "lib"))

from harness_core import (clock, envelope, frontmatter, pipeline,  # noqa: E402
                          policy, registry, roster, schema, workflow)

SCHEMAS = {
    "DesignDoc": {"type": "object", "required": ["criteria", "criteria_ref",
                                                 "test_scenarios", "work_breakdown"],
                  "properties": {
                      "criteria": {"type": "array", "items": {"type": "object",
                          "required": ["id", "what", "how", "pass"]}},
                      "criteria_ref": {"type": "string"},
                      "test_scenarios": {"type": "array", "items": {"type": "string"}},
                      "work_breakdown": {"type": "array", "items": {"type": "object",
                          "required": ["unit_id", "role", "file_scope",
                                       "produces_code"]}}}},
    "ReviewVerdict": {"type": "object",
                      "required": workflow.REVIEW_FIELDS,
                      "properties": {"verdict": {"enum": ["pass", "reject"]}}},
    "BuildReport": {"type": "object", "required": ["unit_id"]},
    "Opinion": {"type": "object", "required": workflow.OPINION_FIELDS},
    "Verdict": {"type": "object",
                "required": ["target_ref", "criteria_frozen", "per_criterion",
                             "abbreviation_check", "opinion_dispositions",
                             "verdict"],
                "properties": {"verdict": {"enum": ["pass", "rework",
                                                    "escalate_design"]}}},
}

PROMPTS = {
    "design": "설계 단계입니다. 원장을 읽고 검증 기준(3요소)·테스트 시나리오·작업 분해를 산출하십시오.",
    "design-review": "적대 설계 리뷰입니다. 무엇을 봤고 **무엇을 보지 않았는지**를 반드시 선언하십시오.",
    "build": "구현 단계입니다. 실패하는 테스트를 먼저 만들고 그 증거를 남긴 뒤 구현하십시오.",
    "opinion": "무전제 관점 의견입니다. 판정하지 마십시오 — 관찰·기대 차이·앵커만 적습니다.",
    "verify": "검증 판정입니다. 기준 항목 전건과 수신 의견 전건을 처분하십시오.",
}


def make_executor(root, req_id, ledger_path, cli, dry=False):
    names = registry.by_role(root)

    def run(*, role, step, inputs, schema):
        if dry:
            return {"_dry": True, "step": step, "role": role}
        payload = {k: v for k, v in inputs.items()}
        prompt = (f"{PROMPTS.get(step, step)}\n"
                  f"인자(JSON): {json.dumps(payload, ensure_ascii=False)}\n"
                  f"첫 행동은 ledger_path 전문 읽기입니다. 프롬프트 요약과 원장이 "
                  f"어긋나면 원장을 따르고 불일치를 보고하십시오.")
        env = {**os.environ, "HARNESS_ROOT": str(root),
               "HARNESS_LIB": str(root / "harness" / "lib"),
               "HARNESS_REQ": req_id, "HARNESS_ROLE": role,
               "HARNESS_PLANE": "workflow",
               "HARNESS_AGENT": names.get(role, role)}
        cmd = [cli, "-p", prompt, "--output-format", "json",
               "--permission-mode", "auto", "--agent", role,
               "--json-schema", json.dumps(SCHEMAS[schema], ensure_ascii=False)]
        p = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True,
                           text=True, timeout=1800)
        try:
            body = json.loads(p.stdout)
            res = body.get("result")
            return json.loads(res) if isinstance(res, str) else res
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(f"{role}/{step} 구조화 출력 파싱 실패: {exc}") from exc
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--cli", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    doc = frontmatter.parse((root / args.ledger).read_text(encoding="utf-8"))
    m = doc.meta
    req = dict(pipeline.new_request_fields())
    req.update({"id": m["id"], "state": "queued", "eligible": True,
                "session_ref": "s-run", "output_kind": m.get("output_kind"),
                "dod_present": bool(doc.section("완료 기준")),
                "priority": m.get("priority", 1), "importance": m.get("importance", 1),
                "weight": m.get("weight", "standard"), "depends_on": [],
                "refs": m.get("refs") or {}})

    cli = args.cli
    if not cli and not args.dry:
        import shutil
        cli = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    pol = policy.load(root)
    machines = [json.loads(p.read_text()) for p in
                (root / "config/machines").glob("*.json")]
    cap = max([mm["limits"]["concurrency_cap"] for mm in machines] or [1])

    out = workflow.run(req, executor=make_executor(root, req["id"], args.ledger,
                                                    cli, dry=args.dry),
                       merge=lambda r, v: {"ok": True, "note": "머지 스텝은 별도"},
                       ledger_path=args.ledger, concurrency_cap=cap, pol=pol)
    print(f"완주 결과: state={out['state']} · 이벤트 {len(out['events'])}건 "
          f"· 병렬 상한 {cap}")
    for e in out["events"]:
        print(f"  {e['event']:24} {json.dumps(e.get('payload', {}), ensure_ascii=False)[:90]}")
    try:
        w = envelope.Writer(root_dir=root, stream="workflow-journal",
                            identity={"machine": "local", "session": "runreq",
                                      "plane": "workflow", "req": req["id"],
                                      "role": "architect"})
        for e in out["events"]:
            w.append(e["event"], e.get("payload") or {})
    except Exception as exc:                          # noqa: BLE001
        print(f"[warn] 저널 착지 실패: {exc}", file=sys.stderr)
    return 0 if out["state"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
