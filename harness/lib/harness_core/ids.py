"""정본: A§2.1 ID 문법 · A§2.2 파일명과 슬러그 · A§3.2 해시 생성식.

채번은 무상태다 — 락·소각 대장·할당기가 존재하지 않는다(A§3.1 후보① 채택).
"재사용을 막는 검사"가 아니라 "재사용이 일어날 메커니즘의 부재"로 강제된다.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import re
import secrets
import time

# A§2.1 TYPE — 닫힌 enum. 이 집합 밖의 값은 채번 자체가 불가능하다.
TYPE_CODES = {"RQ", "AU", "MN", "FL", "FS", "FI", "DN", "AJ", "DS", "HO", "RF"}

# FI 는 시각 없이 맥락 키의 결정론적 해시다(A§2.1 예외 문법).
_TIMED_TYPES = TYPE_CODES - {"FI"}

_ID_RE = re.compile(
    r"^(?P<type>RQ|AU|MN|FL|FS|DN|AJ|DS|HO|RF)"
    r"-(?P<date>\d{8})T(?P<time>\d{6})Z"
    r"-(?P<hash>[0-9a-f]{8})$"
)
_FI_RE = re.compile(r"^FI-[0-9a-f]{12}$")

# A§2.2 SLUG := [a-z0-9] ( [a-z0-9-]{0,38} [a-z0-9] )?   # 최대 40자
SLUG_MAX = 40
SLUG_MIN_MEANINGFUL = 3

# A§1.4 파일 형식은 유형 계약이다.
EXT_BY_TYPE = {
    "RQ": "md", "MN": "md", "FL": "md", "DN": "md",
    "AJ": "md", "DS": "md", "HO": "md", "RF": "md",
    "AU": "jsonl", "FS": "jsonl",
}
ALLOWED_EXT = {"md", "jsonl"}


@dataclasses.dataclass(frozen=True)
class ParsedId:
    raw: str
    type_code: str
    dt: _dt.datetime          # tz-aware, UTC
    hash8: str


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def mint(type_code: str, *, machine_id: str, session_id: str,
         now: _dt.datetime | None = None) -> str:
    """A§3.2 — HASH8 = SHA-256(machine ‖ session ‖ ts_ns ‖ rand128 ‖ TYPE)[0:8].

    조정 통신 0회. 같은 UTC 초 안에서의 충돌 확률은 rand128 이 지배한다.
    """
    if type_code not in _TIMED_TYPES:
        raise ValueError(
            f"unknown or non-timed type code: {type_code!r} "
            f"(FI 는 incident_id() 로 만든다)"
        )
    if not machine_id or not session_id:
        raise ValueError("machine_id·session_id 는 빈 값일 수 없다(신원 전파 — S§7)")

    stamp = (now or _utc_now()).astimezone(_dt.timezone.utc)
    material = "‖".join([
        machine_id,
        session_id,
        str(time.time_ns()),
        secrets.token_hex(16),          # rand128
        type_code,
    ])
    h = hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
    return f"{type_code}-{stamp:%Y%m%dT%H%M%S}Z-{h}"


def incident_id(context_key: str) -> str:
    """A§3.2 — 같은 맥락 키는 언제 어디서 계산해도 같은 사건 ID 로 수렴한다.

    동일 주체·맥락·위반을 사건 1건에 접는 요구(S§7·S§9.1-4)를 채번 수준에서
    구현하는 것이다. 재탐지는 새 사건이 아니라 카운트 델타가 된다.
    """
    if not context_key:
        raise ValueError("context_key 가 비면 사건 동일성 판정이 성립하지 않는다")
    return "FI-" + hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:12]


def parse(doc_id: str) -> ParsedId:
    m = _ID_RE.match(doc_id or "")
    if not m:
        raise ValueError(f"ID 문법 위반(A§2.1): {doc_id!r}")
    date, tm = m.group("date"), m.group("time")
    try:
        dt = _dt.datetime.strptime(date + tm, "%Y%m%d%H%M%S").replace(
            tzinfo=_dt.timezone.utc
        )
    except ValueError as exc:
        raise ValueError(f"ID 의 시각 성분이 실재 시각이 아니다: {doc_id!r}") from exc
    return ParsedId(doc_id, m.group("type"), dt, m.group("hash"))


def is_incident_id(value: str) -> bool:
    return bool(_FI_RE.match(value or ""))


def type_of(doc_id: str) -> str:
    if is_incident_id(doc_id):
        return "FI"
    return parse(doc_id).type_code


def slugify(title: str) -> str:
    """A§2.2 — 소문자화 → ASCII 영숫자 외 하이픈 → 연속 압축 → 40자 절단.

    비ASCII 제목은 로마자 변환을 시도하지 않는다: 플랫폼별 유니코드 정규화
    차이가 파일명 축에 들어오는 것을 원천 차단한다(S§4.3 실행 환경 불문).
    유의미 문자 3자 미만이면 생략한다 — 무의미 조각을 방출하느니 ID 만 남긴다.
    """
    if not title:
        return ""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if len(s) > SLUG_MAX:
        cut = s[:SLUG_MAX]
        # 단어 경계 우선 — 마지막 하이픈에서 자르되, 그러면 3자 미만이 되는
        # 경우에는 경계를 포기하고 길이를 지킨다(길이가 계약, 경계는 선호다).
        boundary = cut.rfind("-")
        if boundary >= SLUG_MIN_MEANINGFUL:
            cut = cut[:boundary]
        s = cut.strip("-")
    if len(re.sub(r"-", "", s)) < SLUG_MIN_MEANINGFUL:
        return ""
    return s


def filename(doc_id: str, title: str = "", ext: str | None = None) -> str:
    """A§2.2 — FILENAME := ID [ "-" SLUG ] "." EXT

    ext 를 주지 않으면 유형 계약(A§1.4)에서 파생한다. 호출자가 형식을 고르는
    경로를 남기면 E10(카테고리 파일 형식 계약 위반)이 런타임에서만 잡힌다.
    """
    parsed_type = type_of(doc_id)
    resolved = ext or EXT_BY_TYPE.get(parsed_type)
    if resolved not in ALLOWED_EXT:
        raise ValueError(f"EXT 계약 위반(A§2.2): {resolved!r}")
    if ext and EXT_BY_TYPE.get(parsed_type) and ext != EXT_BY_TYPE[parsed_type]:
        raise ValueError(
            f"유형 {parsed_type} 의 파일 형식은 {EXT_BY_TYPE[parsed_type]} 다(A§1.4)"
        )
    slug = slugify(title)
    return f"{doc_id}-{slug}.{resolved}" if slug else f"{doc_id}.{resolved}"
