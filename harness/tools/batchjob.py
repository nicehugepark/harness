#!/usr/bin/env python3
"""배치 잡 실행체 — 커스텀 잔량 #3 (A§5.4-⑤).

정본: A§5.3 배치 커밋 · A§5.4-⑤ · A§7.3 컴팩션 · A§9 증분 미러.

**상주 프로세스가 아니다.** 트리거(Stop 훅 · 주기 실행) 시점에 1회 돌고 끝난다.
멱등이라 어느 단계가 죽어도 다음 주기가 이어받는다.

  batchjob.py            전 단계 실행
  batchjob.py --dry      판정만 하고 쓰지 않는다
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "harness" / "lib"))

from harness_core import batch, clock, envelope  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="배치 잡 (A§5.4-⑤)")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    if args.dry:
        out = {"untracked": batch.find_untracked(root),
               "tmp": [str(p) for p in root.rglob("*.tmp")],
               "reality": batch.verify_commit_reality(root)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    out = batch.run_all(root)
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events",
                            identity={"machine": "local", "session": "batch",
                                      "plane": "system"})
        w.append("batch.run", {k: v for k, v in out.items() if k != "reality"})
        if out["reality"]["missing"]:
            # 미커밋 문서는 결함 상태다 — 조용히 넘기지 않는다
            w.append("batch.defect", {"missing": out["reality"]["missing"][:20]})
    except Exception as exc:                          # noqa: BLE001
        print(f"[warn] 감사 이벤트 실패: {exc}", file=sys.stderr)

    print(f"배치 잡 {out['at']}")
    print(f"  tmp 청소   : {out['tmp_swept']}건")
    if out.get("untracked_absorbed"):
        print(f"  미추적 흡수: {len(out['untracked_absorbed'])}건")
    c = out["commit"]
    print(f"  배치 커밋  : {'완료 ' + str(len(c['files'])) + '건' if c['committed'] else c['reason']}")
    print(f"  커밋 실재  : 미보존 {len(out['reality']['missing'])}건")
    print(f"  인덱스     : {out['index']}")
    print(f"  증분 미러  : {out['mirror']['copied']}건")
    return 1 if out["reality"]["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
