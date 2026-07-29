#!/usr/bin/env python3
"""발화 수신 시각 실측 주입 + prompt.submit 이벤트(원문 미수록, 길이·digest만)

자산 선언은 동반 hook.json 에 있다 — 실행체 안에 주석으로 쓰지 않는다
(파서가 언어별로 갈라지고, 주석은 썩는다 — S§9.3-13).
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, os.environ.get("HARNESS_LIB")
                or str(pathlib.Path(__file__).resolve().parents[3] / "lib"))

from harness_core import clock, envelope, hooks as H, policy, registry, roster


@H.safe
def main():
    import hashlib
    payload = H.read_payload()
    root = H.root_dir(payload)
    ident = H.identity(payload)
    now = clock.now_utc()
    state = root / "config" / "local" / "turn-clock.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"turn_started_at": clock.iso_utc(now)}), encoding="utf-8")
    prompt = str(payload.get("prompt") or "")
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events", identity=ident)
        w.append("prompt.submit", {"len": len(prompt),
                 "digest": hashlib.sha256(prompt.encode()).hexdigest()[:16]})
    except Exception:
        pass
    H.emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
            "additionalContext": f"측정 시각(실측): {clock.iso_local(clock.now_local())}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
