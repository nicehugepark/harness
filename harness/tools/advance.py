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

    req = dict(pipeline.new_request_fields())
    req.update({k: v for k, v in meta.items() if k in req})
    req["state"] = schema.read_state(meta)
    req["refs"] = meta.get("refs") or {}
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
