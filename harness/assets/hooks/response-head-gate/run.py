#!/usr/bin/env python3
"""응답 머리 표기 3요소·시각 드리프트·상찬 어휘 차단 + 존댓말 관측(X8·F§7.9)

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

# 아래는 선언이 아니라 **식별용 고정 문자열**이다. 선언 블록은 동반 hook.json 에만
# 둔다(E§2.5.1). 이 표식이 없으면 설치기가 이 파일을 관리물로 판정하지 못해
# 증분 갱신에서 '사용자 커스텀'으로 보류되고 초기화에서도 남는다
# (실측 2026-07-29T11:27 — 증분 설치 보류 5건, 수정본이 배치되지 않았다).
HARNESS_MANAGED = "harness-managed-asset/v1"


@H.safe
def main():
    payload = H.read_payload()
    root = H.root_dir(payload)
    ident = H.identity(payload)
    pol = policy.load(root)
    text = H.response_text(payload)
    injected = None
    st = root / "config" / "local" / "turn-clock.json"
    if st.exists():
        try:
            injected = clock.parse_iso(json.loads(st.read_text())["turn_started_at"])
        except Exception:
            injected = None
    names = registry.display_names(root)
    ok, why = H.check_head(text, registry_names=names,
                           role_keys=set(roster.ROLE_KEYS),
                           drift_cap_s=int(pol.get("head_time_drift_s", 900)),
                           injected_at=injected)
    block = payload.get("stop_hook_active")
    gate_block = H.extract_gate_block(text)
    praise = H.scan_praise(gate_block, pol.get("praise_lexicon", []))
    honor = H.observe_honorific(text)
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events", identity=ident)
        w.append("agent.turn_end", {"head_ok": ok, "head_detail": why[:200],
                                    "praise_hit": praise, "honorific": honor})
    except Exception:
        pass
    if not block:
        if praise:
            H.block_stop(f"판정·리뷰 구조화 필드에 상찬 어휘가 있습니다: {praise!r}. "
                         f"사실·행동·근거로 다시 쓰십시오(S§2-19).")
            return 0
        if not text.strip():
            # 판정 불가 — 위반이 아니다. 사실만 남기고 통과시킨다(S§7).
            pass
        elif not ok:
            H.block_stop(why)
            return 0
    H.emit({})
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
