"""정본: A§4.1 공통 필드 · A§4.2 유형별 추가 필드 · A§4.3 스키마 진화 · A§6.2 상태 enum.

시정 반영: X1(output_kind·doc_evidence) · X3(criteria_delta) · X5(root)
          · X6(design_evidence) · X7(status→state 단일화, schema 2)

이 모듈은 "무엇이 필수인가"만 안다. "그래서 거부인가"는 gate.py 가 정한다 —
필드 계약과 집행을 한 곳에 두면 계약을 읽으려는 사람이 집행 코드를 읽게 된다.
"""
from __future__ import annotations

SUPPORTED_SCHEMAS = {1, 2}
CURRENT_SCHEMA = 2

# ── A§4.1 공통 필수 필드 (schema 2) ──────────────────────────────
# `status` 는 X7 로 폐기됐다. `state` 가 유일한 상태 자리다.
COMMON_REQUIRED = [
    "schema", "id", "type", "title", "visibility", "state",
    "root",                      # X5
    "created", "updated", "machine", "session",
    "author", "requester", "what", "why", "tags",
]

# 자동 주입 필드 — 사람·에이전트가 쓰지 않는다(S§3.5 · A§4.1 R5).
AUTO_INJECTED = ["machine", "session", "created", "updated", "author",
                 "requester", "state", "root", "id", "schema", "type"]

# 사람이 생성 인자로만 채우는 필드(A§4.1 R6 — 입력구가 게이트보다 먼저다).
HUMAN_CREATION_ARGS = ["title", "visibility", "what", "why"]

# schema 1 에서 쓰던 폐기 키 — schema 2 에서 존재 자체가 위반이다.
RETIRED_KEYS_V2 = ["status", "closed_as"]

# ── A§6.2 상태 enum (X7 단일화) ─────────────────────────────────
STATE_ENUM = {
    # RQ 의 정본은 B§1.1 — 여기서는 인용만 한다.
    "RQ": ["received", "analyzed", "queued", "designing", "building",
           "verifying", "merging", "done", "hold", "failed", "void"],
    "AJ": ["open", "notified", "answered", "adopted", "void"],
    "DN": ["active", "superseded"],
    # 공통 골격 — closed_done 은 X7 로 삭제됐다(RQ 외에 '정상 종결'이 없다).
    "_common": ["active", "superseded", "closed_void"],
}
for _t in ("MN", "DS", "HO", "RF", "FL", "AU", "FS"):
    STATE_ENUM.setdefault(_t, STATE_ENUM["_common"])

# ── A§4.2 유형별 추가 필수 필드 ─────────────────────────────────
TYPE_REQUIRED = {
    "RQ": ["priority", "weight", "depends_on", "output_kind"],   # output_kind = X1
    "MN": ["participants"],
    "FL": ["incidents"],
    "DN": ["decided_by", "device_status"],
    "AJ": ["question", "reason", "options", "deadline", "escalation",
           "refs"],
    "DS": ["stage"],
    "HO": ["resume"],
    "RF": ["source"],
}

DS_STAGES = ["analysis", "design", "dev", "verification"]

# ── A§4.2 본문 필수 절 ──────────────────────────────────────────
REQUIRED_SECTIONS = {
    "RQ": ["요약", "원문", "완료 기준"],
    "MN": ["요약", "합의", "원문"],
    "_default": ["요약"],
}
DS_STAGE_SECTIONS = {
    "design": ["요약", "검증 기준", "테스트 시나리오"],
}

# ── X1 증거 계약 ────────────────────────────────────────────────
OUTPUT_KINDS = ["code", "document", "mixed"]

EVIDENCE_FIELDS = {
    "tdd": [
        "failing_test_ref", "failing_run_excerpt", "impl_ref",
        "test_run_output_ref", "deviation_disclosure",
    ],
    "doc": [
        "criteria_ref", "pre_check_ref", "pre_check_excerpt", "impl_ref",
        "post_check_output_ref", "deviation_disclosure",
    ],
}
EVIDENCE_KEY = {"tdd": "tdd_evidence", "doc": "doc_evidence"}

# X6 — 화면·시각 산출 증거
DESIGN_EVIDENCE_FIELDS = [
    "layout_rationale", "typography_rationale", "color_rationale",
    "render_evidence_ref", "deviation_disclosure",
]

# ── A§7.1 통제 어휘 ─────────────────────────────────────────────
TAG_VOCAB = {
    "stage": ["intake", "analysis", "design", "build", "verify", "closeout", "dev"],
    "origin": ["human", "agent", "rollup"],
    "kind": ["defect", "improvement", "question", "incident", "decision",
             "upgrade", "external-deliverable"],
    "domain": ["harness-self", "document-system", "pipeline", "agents", "audit",
               "installer", "dashboard", "external-project"],
    "component": ["numbering", "index", "gate", "backup", "vault", "notifier",
                  "queue", "registry", "tag-vocabulary", "stream-registry",
                  "installer", "agents", "lead-gate", "clock"],
}
UNCLASSIFIED_PREFIX = "unclassified/"

# ── refs 슬롯 기대 유형 (A§5.2 E08) ─────────────────────────────
REF_SLOT_TYPES = {
    "request": "RQ", "analysis": "DS", "design": "DS", "dev": "DS",
    "verification": "DS", "spec": "RF", "parts": None, "adjudication": "AJ",
    "adjudications": "AJ", "decision": "DN", "followup": "RQ",
}


def read_state(meta: dict) -> str | None:
    """A§4.3 판별 분기 — schema 1 은 `status`, schema 2 는 `state`."""
    if int(meta.get("schema", CURRENT_SCHEMA)) <= 1:
        return meta.get("status") or meta.get("state")
    return meta.get("state")


def evidence_required(output_kind: str, *, unit_produces_code) -> str:
    """X1 (c) — 증거 요구는 산출 유형으로 분기한다. 어느 분기에도 증거 0 은 없다."""
    if output_kind not in OUTPUT_KINDS:
        raise ValueError(
            f"output_kind 는 {OUTPUT_KINDS} 중 하나다(X1): {output_kind!r}"
        )
    if output_kind == "code":
        return "tdd"
    if output_kind == "document":
        return "doc"
    if unit_produces_code is None:
        raise ValueError(
            "output_kind=mixed 는 유닛의 produces_code 없이 판정할 수 없다(X1-b)"
        )
    return "tdd" if unit_produces_code else "doc"


def required_fields(meta: dict) -> list[str]:
    ver = int(meta.get("schema", CURRENT_SCHEMA))
    common = list(COMMON_REQUIRED)
    if ver <= 1:
        common = [("status" if f == "state" else f) for f in common]
        common = [f for f in common if f != "root"]
    fields = common + list(TYPE_REQUIRED.get(meta.get("type"), []))

    if meta.get("type") == "DS" and meta.get("stage") == "dev":
        # 증거 키는 output_kind 로 갈리지만 원장을 읽지 않는 게이트도 있어,
        # "둘 중 하나는 반드시 있다"를 여기서 표현한다(gate 가 택일을 검사).
        pass
    return fields


def missing_required(meta: dict) -> list[str]:
    out = []
    for f in required_fields(meta):
        v = meta.get(f)
        if v is None or (isinstance(v, (str, list, dict)) and len(v) == 0):
            out.append(f)
    return out


def retired_keys_present(meta: dict) -> list[str]:
    if int(meta.get("schema", CURRENT_SCHEMA)) <= 1:
        return []
    return [k for k in RETIRED_KEYS_V2 if k in meta]


def required_sections(meta: dict) -> list[str]:
    t = meta.get("type")
    if t == "DS":
        return DS_STAGE_SECTIONS.get(meta.get("stage"), REQUIRED_SECTIONS["_default"])
    return REQUIRED_SECTIONS.get(t, REQUIRED_SECTIONS["_default"])


def normalize_tags(tags) -> tuple[list[str], list[str]]:
    """A§7.2 — 어휘 밖 값은 거부하지 않고 `unclassified/` 로 치환해 표면화한다.

    거부하면 태그 기입 자체가 소멸한다(S§3.6 실측: 96% 빈 태그).
    """
    if not isinstance(tags, list) or not tags:
        return [], []
    ok, moved = [], []
    for t in tags:
        s = str(t)
        if s.startswith(UNCLASSIFIED_PREFIX):
            ok.append(s)
            continue
        axis, _, value = s.partition("/")
        if value and axis in TAG_VOCAB and value in TAG_VOCAB[axis]:
            ok.append(s)
        else:
            slug = s.replace("/", "-").lower()
            ok.append(UNCLASSIFIED_PREFIX + slug)
            moved.append(s)
    return ok, moved


def state_valid(meta: dict) -> bool:
    st = read_state(meta)
    allowed = STATE_ENUM.get(meta.get("type"), STATE_ENUM["_common"])
    return st in allowed
