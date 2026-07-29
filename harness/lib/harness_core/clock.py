"""정본: S§2-20(응답 머리 표기·시각 실측) · A§4.1(created/updated 자동 주입) · X8.

시각은 추정하지 않고 실측한다. 이 모듈이 그 실측의 유일한 원어이며,
에이전트·사람이 시각 문자열을 손으로 조립하는 경로를 두지 않는다
(S§3.5 — 사람이 쓰면 누락과 오기가 표준이 된다).
"""
from __future__ import annotations

import datetime as _dt
import os

# X8 8.4-c — 훅 주입 시각과 응답 표기 시각의 허용 드리프트(초).
# 잠정값이다: 긴 사고·긴 실행이 정상이므로(S§4.4 응답 대기 무제한) 짧게 걸면
# 정당한 응답이 차단된다. 보정 신호는 정당 응답의 차단율이며 미검증 제안이다.
DEFAULT_HEAD_TIME_DRIFT_S = 900

# 훅이 턴 시작 시각을 주입하는 환경 변수(D§1.2 — 에이전트가 재지 않게 한다).
ENV_TURN_STARTED_AT = "HARNESS_TURN_STARTED_AT"


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def now_local() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def iso_utc(dt: _dt.datetime | None = None) -> str:
    """ISO 8601, UTC, 초 단위. 감사 레코드의 `ts` 형식(D§2.2)."""
    d = (dt or now_utc()).astimezone(_dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_local(dt: _dt.datetime | None = None) -> str:
    """ISO 8601, 로컬 오프셋 포함, 초 단위. frontmatter 의 created/updated 형식."""
    d = dt or now_local()
    if d.tzinfo is None:
        d = d.astimezone()
    return d.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + d.strftime("%z")[-2:]


def id_stamp(dt: _dt.datetime | None = None) -> str:
    """A§2.1 — ID 의 시각 성분(UTC)."""
    return (dt or now_utc()).astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_iso(value: str) -> _dt.datetime:
    """ISO 8601 파싱. 오프셋 없는 값은 거부한다 — 시각의 절대성이 계약이다."""
    if not value:
        raise ValueError("빈 시각 문자열")
    v = value.strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(v)
    except ValueError as exc:
        raise ValueError(f"ISO 8601 위반: {value!r}") from exc
    if d.tzinfo is None:
        raise ValueError(f"오프셋 없는 시각은 무효다(S§3.5): {value!r}")
    return d


def turn_started_at() -> _dt.datetime | None:
    """훅이 주입한 턴 시작 실측 시각. 부재면 None 이며 게이트가 그 사실을 판정한다."""
    raw = os.environ.get(ENV_TURN_STARTED_AT)
    if not raw:
        return None
    try:
        return parse_iso(raw)
    except ValueError:
        return None


def drift_seconds(head_at: str, injected: _dt.datetime | None = None) -> float | None:
    """머리 표기 시각과 훅 주입 실측 시각의 드리프트.

    주입값이 없으면 None — 게이트는 이것을 통과가 아니라 '대조 불가'로 다루고,
    그 사실을 침묵이 아니라 이벤트로 남긴다(S§7 — 침묵과 무위반의 구별).
    """
    base = injected or turn_started_at()
    if base is None:
        return None
    return abs((parse_iso(head_at) - base).total_seconds())


def head_line(display_name: str, role_key: str,
              at: _dt.datetime | None = None) -> str:
    """S§2-20 — 응답 머리 표기 3요소. 문자열 조립은 이 함수 하나로만 한다.

    사람·에이전트가 포맷을 손으로 맞추면 표기 이형이 발생하고, 그것이
    선행 운영에서 134건 난 자리다(S§10-6).
    """
    if not display_name:
        raise ValueError("표시명이 비었다 — 역할 키 단독 표기는 금지다(S§3.5)")
    if not role_key:
        raise ValueError("역할 키가 비었다")
    return f"[{iso_local(at)} · {display_name} · {role_key}]"
