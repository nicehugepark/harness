"""정본: D§4 의사결정 문서 · D§5 3채널 동시 통지 · D§6 에스컬레이션 체커
      · A§4.2 AJ 필드 자리.

3채널은 **동시 발송이며 폴백이 아니다.** 수신 단말 존부를 판정하지 않는다 —
항상 발송하고, 단말이 없으면 그 채널만 미전달로 남는다. 전달 결과를 채널별로
기록해 침묵과 미전달을 구별한다.

재시도 루프가 이 모듈에 없다(R31). 미전달 회수는 에이징이 담당한다 —
경고 나열식 재발송은 소비되지 않았다는 실측이 근거다.
"""
from __future__ import annotations

import re
import subprocess

from . import clock, policy

# A§4.2 — AJ 유형 고유 필드. **기본 집행 필드가 없다**(존재 차원의 차단):
# deadline 경과는 어떤 선택지도 자동 실행하지 않는다.
ADJUDICATION_FIELDS = ["question", "reason", "options", "deadline", "escalation",
                       "notify", "refs", "response", "responded_by"]
REASONS = ["hitl-gate", "starvation", "lead-gate", "budget", "other"]
STATES = ["open", "notified", "answered", "adopted", "void"]

# D§6.2 백오프 사다리 — 정책 제안값. 상한 없이 반복하며 사람 응답만이 종결한다.
BACKOFF_MIN = policy.DEFAULTS["escalation_backoff_min"]

_CRED = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+")


def validate_adjudication(adj: dict) -> tuple[bool, str]:
    if not str(adj.get("question", "")).strip():
        return False, "question 은 1문장 필수다(배경은 본문)"
    if adj.get("reason") not in REASONS:
        return False, f"reason enum 밖: {adj.get('reason')!r} — 허용 {REASONS}"
    if not adj.get("options"):
        return False, ("options 는 ≥1 이다 — 자유 응답이 상시 허용이므로 최소 1로 "
                       "완화하되, 선택지 0은 질문이 성립하지 않는다")
    return True, ""


def render_short(adj: dict) -> str:
    """R34 — 값·자격증명·실주소를 싣지 않는다. 요약 필드와 문서 경로만 읽는다."""
    parts = [f"[의사결정 요청] {adj.get('id')}", str(adj.get("question", ""))]
    for o in adj.get("options") or []:
        parts.append(f"  · {o.get('oid')}: {o.get('label')}")
    if adj.get("deadline"):
        parts.append(f"  마감 {adj['deadline']}")
    if adj.get("path"):
        parts.append(f"  문서 {adj['path']}")
    msg = "\n".join(parts)
    return _CRED.sub("[자격증명 패턴 제거]", msg)


def terminal_channel(msg: str, command: str | None = None) -> bool:
    """OS 알림 명령. 실측: 이 환경에 notify-send 는 없고 Windows 토스트 경로가 있다."""
    if not command:
        return False
    try:
        r = subprocess.run(command, shell=True, input=msg, text=True,
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notify(adj: dict, *, level: int, channels: dict, append) -> dict:
    """D§5.5 통지 파이프.

    불변식 ①채널2(데이터 착지)는 append 그 자체다 — 실패하면 발송 전체가 실패다
    (기록 없는 발송 금지) ②채널 간 폴백·순서 의존 없음 ③재시도 루프 없음.
    """
    msg = render_short(adj)
    append({"event": "adjudication.notify", "adj_ref": adj.get("id"),
            "level": level, "channels": ["terminal", "dashboard", "mobile"]})
    delivered = {"dashboard": True}
    for name, fn in channels.items():
        try:
            delivered[name] = bool(fn(msg))
        except Exception:                            # noqa: BLE001
            delivered[name] = False
        append({"event": "notify.delivery", "adj_ref": adj.get("id"),
                "ch": name, "ok": delivered[name]})
    return {"message": msg, "delivered": delivered, "level": level}


def backoff_minutes(level: int) -> int:
    idx = min(max(level, 1), len(BACKOFF_MIN)) - 1
    return BACKOFF_MIN[idx]


def escalate(adj: dict, *, now: str) -> dict:
    """R28 — 재통지는 **같은 문서의 level 상승**이지 새 문서·새 사건이 아니다.

    미응답 1건 = 의사결정 문서 1건이다. 나열식 방치 알림은 소비되지 않았다.
    """
    esc = dict(adj.get("escalation") or {"level": 0})
    if adj.get("state") not in ("open", "notified"):
        return {**adj, "escalation": esc, "side_effects": {}}
    nxt = esc.get("next_check_at")
    if nxt and clock.parse_iso(now) < clock.parse_iso(nxt):
        return {**adj, "escalation": esc, "side_effects": {}}
    esc["level"] = int(esc.get("level", 0)) + 1
    esc["last_notified_at"] = now
    mins = backoff_minutes(esc["level"])
    esc["next_check_at"] = clock.iso_utc(
        clock.parse_iso(now) + __import__("datetime").timedelta(minutes=mins))
    side = {}
    if esc["level"] >= 2:
        # 스케줄러가 착수 대상에서 제외하되 에이징 계수는 계속 증가시킨다
        side["blocked_on"] = "human"
    if esc["level"] >= 3:
        side["pin_to_rollup"] = True
    return {**adj, "escalation": esc, "side_effects": side}
