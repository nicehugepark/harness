#!/usr/bin/env python3
"""전이 적용 실행체 — 세션이 원장에 상태를 쓰는 **유일 경로**.

정본: B§1.3 전이표 · B§1.5 B-1(이벤트 동반 원자 수행) · B§3.6 lease fencing.

세션에 절차만 주고 상태 쓰기 도구를 주지 않았더니, 세션이 옳게 진행해도 원장이
바뀌지 않아 요청이 designing 에서 영구히 멈췄다(실사고 2026-07-29T05:05).

  advance.py --ledger <경로> --to building --lease <세션ref> [--payload p.json]

가드 불통과면 아무것도 바꾸지 않고 종료 코드 1. lease 불일치면 fencing 거부.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import (clock, envelope, frontmatter, ledger,  # noqa: E402
                          pipeline, schema)


def _repo_root_id(root: pathlib.Path) -> str:
    """X5 — land.py 와 같은 규칙. 두 곳이 다른 값을 내면 게이트가 자기 문서를 거부한다."""
    import hashlib
    if not (root / ".git").exists():
        return "bootstrap"
    r = subprocess.run(["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
                       capture_output=True, text=True)
    first = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not first:
        return "bootstrap"
    return hashlib.sha256(first[-1].encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--to", required=True, choices=pipeline.STATES)
    ap.add_argument("--lease", default=os.environ.get("HARNESS_SESSION"))
    ap.add_argument("--payload", help="전이 payload JSON 파일(셸 비경유)")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    p = root / args.ledger

    doc = frontmatter.parse(p.read_text(encoding="utf-8"))
    meta = dict(doc.meta)

    if not ledger.lease_ok(meta, args.lease):
        print(json.dumps({"ok": False, "why": "lease 불일치 — fencing 거부",
                          "have": meta.get("session_ref"), "given": args.lease},
                         ensure_ascii=False))
        return 1

    ctx = {}
    if args.payload:
        ctx = json.loads(pathlib.Path(args.payload).read_text(encoding="utf-8"))

    # 전이 2 의 payload(analysis_doc_ref·weight 등)는 원장 frontmatter 에 없는
    # 값이라 여기서 받아 **같은 원자 쓰기 안에서** 기입한다. 분석 세션이 문서만
    # 남기고 스케줄링 필드를 못 쓰면 g_analyzed 가 영원히 결손을 보고, 요청은
    # received 에 갇힌다.
    SCHED_FIELDS = ("priority", "importance", "weight", "output_kind",
                    "depends_on")
    if args.to == "analyzed":
        for f in SCHED_FIELDS:
            if f in ctx:
                meta[f] = ctx[f]
        if ctx.get("analysis_doc_ref"):
            meta.setdefault("refs", {})
            meta["refs"] = {**(meta.get("refs") or {}),
                            "analysis": ctx["analysis_doc_ref"]}
        # schema 1 문서는 이 시점에 2 로 올린다 — 스케줄링 필드를 새로 쓰는
        # 자리가 유일하게 여기이고, 여기서 안 올리면 `status`/`state` 두 자리가
        # 계속 공존해 어느 쪽이 정본인지 판정할 수 없다(X7).
        if int(meta.get("schema", 2)) <= 1:
            meta["schema"] = schema.CURRENT_SCHEMA
            meta["state"] = meta.pop("status", schema.read_state(meta))
            meta.setdefault("root", _repo_root_id(root))

    req = dict(pipeline.new_request_fields())
    req.update({k: v for k, v in meta.items() if k in req})
    req["state"] = schema.read_state(meta)
    req["refs"] = meta.get("refs") or {}
    # 완료 기준의 실재는 세션의 주장이 아니라 문서의 사실이다 — 본문에서 읽는다.
    req["dod_present"] = "## 완료 기준" in doc.body
    for f in SCHED_FIELDS:
        if meta.get(f) is not None:
            req[f] = meta[f]
    # 가드가 요구하는 최소 사실을 payload 에서 받는다 — 세션이 자기 산출을
    # 자기 승인하지 못하게, 이 값들은 gatecheck 통과 후에만 채워진다.
    for k in ("design_landed", "adversarial_review_passed", "units",
              "merge_commit_observed", "ancestry_confirmed", "output_kind"):
        if k in ctx:
            req[k] = ctx[k]
    req.setdefault("output_kind", meta.get("output_kind"))

    try:
        new, event = pipeline.apply(req, args.to, ctx)
    except pipeline.TransitionRefused as exc:
        print(json.dumps({"ok": False, "why": str(exc)}, ensure_ascii=False))
        return 1

    for f in ("state", "rework_count", "escalation_count", "design_review_count",
              "merge_retry_count", "criteria_version"):
        meta[f if f != "state" else
             ("status" if int(meta.get("schema", 2)) <= 1 else "state")] = new[f]
    meta["updated"] = clock.iso_local()
    if args.to in pipeline.TERMINAL_STATES:
        meta["session_ref"] = None
    if args.to == "queued":
        # queued 의 정의가 "스케줄러가 아직 집지 않은 것"이다. lease 를 남기면
        # can_claim 이 점유 중으로 보고 영영 집지 않는다 — 분석 lane 이 만든
        # 요청이 designing 으로 못 넘어간 자리가 여기다.
        meta["session_ref"] = None

    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(frontmatter.render(meta, doc.body))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", args.ledger],
                       capture_output=True, text=True)
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events",
                            identity={"machine": os.environ.get("HARNESS_MACHINE",
                                                                "local"),
                                      "session": args.lease, "plane": "workflow",
                                      "req": meta.get("id"),
                                      "role": os.environ.get("HARNESS_ROLE",
                                                             "architect")})
        w.append(event["event"], event.get("payload") or {})
    except Exception:                                  # noqa: BLE001
        pass
    print(json.dumps({"ok": True, "state": new["state"], "event": event["event"]},
                     ensure_ascii=False))

    # 전이 4 는 전이표에 "자동(2 직후)"로 적혀 있다. 세션이 따로 호출하게 두면
    # 호출을 빠뜨린 요청이 analyzed 에 갇히고, 그 상태는 스케줄러의 배분 대상도
    # 인테이크 lane 의 대상도 아니라 아무도 집어가지 않는다.
    if args.to == "analyzed":
        rc = subprocess.run(
            [sys.executable, __file__, "--root", str(root),
             "--ledger", args.ledger, "--to", "queued", "--lease", args.lease]
            + (["--payload", args.payload] if args.payload else []),
            capture_output=True, text=True)
        sys.stdout.write(rc.stdout)
        if rc.returncode:
            sys.stderr.write(rc.stderr)
            return rc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
