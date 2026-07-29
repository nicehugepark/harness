#!/usr/bin/env python3
"""요청 세션 종료를 기록하고 통지 발신을 **예약**한다.

훅은 네트워크를 하지 않는다(R33) — 훅 지연이 전 도구 호출을 지연시키기 때문이다.
그래서 이 훅은 발신 대기 레코드만 남기고, 실제 발송은 배치 잡이 한다.
'끝났다는 사실'과 '알렸다는 사실'을 다른 시점에 두되 둘 다 기록에 남긴다.

자산 선언은 동반 hook.json 에 있다.

# 아래는 선언이 아니라 식별용 고정 문자열이다.
HARNESS_MANAGED = "harness-managed-asset/v1"
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, os.environ.get("HARNESS_LIB")
                or str(pathlib.Path(__file__).resolve().parents[3] / "lib"))

from harness_core import clock, envelope, hooks as H


@H.safe
def main():
    payload = H.read_payload()
    root = H.root_dir(payload)
    ident = H.identity(payload)
    req = ident.get("req")
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events", identity=ident)
        w.append("session.ended", {"reason": payload.get("reason"),
                                   "req": req})
    except Exception:
        pass
    if req:
        # 요청 세션이 끝났다 — 발신 대기 레코드를 남긴다(발송은 배치 잡).
        q = root / "config" / "local" / "notify-queue.jsonl"
        q.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": clock.iso_utc(), "kind": "session-end", "req": req,
               "session": ident.get("session"), "machine": ident.get("machine"),
               "state": "pending"}
        with open(q, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    H.emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
