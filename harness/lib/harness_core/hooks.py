"""정본: A§5.4 훅 배선 · D§1.2 훅 설계표 · F§3.2 리드 게이트 판정식 · X8 머리 표기.

훅 실행체는 전부 이 모듈의 함수를 부르는 얇은 래퍼다. 판정 본문을 실행체에
흩으면 같은 판정의 사본이 발산한다(S§9.6-35).

이 모듈에는 네트워크·커밋·파생 재생성 원어가 없다(R24·R33). 훅이 그것을 하지
않는다는 것은 규율이 아니라 **부재**로 성립한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

from . import clock, policy

# ── 공통 ────────────────────────────────────────────────────────
ENV_ROOT = "HARNESS_ROOT"
ENV_SESSION = "HARNESS_SESSION"
ENV_ROLE = "HARNESS_ROLE"
ENV_PLANE = "HARNESS_PLANE"
ENV_REQ = "HARNESS_REQ"
ENV_AGENT = "HARNESS_AGENT"
ENV_MACHINE = "HARNESS_MACHINE"


def read_payload() -> dict:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}


def root_dir(payload: dict) -> pathlib.Path:
    return pathlib.Path(os.environ.get(ENV_ROOT) or payload.get("cwd") or ".")


def identity(payload: dict) -> dict:
    """D§3 — 신원은 기동 주체가 발급하고 훅은 전파만 한다."""
    import platform
    return {
        "machine": os.environ.get(ENV_MACHINE) or platform.node(),
        "session": payload.get("session_id") or os.environ.get(ENV_SESSION) or "",
        "plane": os.environ.get(ENV_PLANE, "interactive"),
        "req": os.environ.get(ENV_REQ),
        "role": os.environ.get(ENV_ROLE),
        "agent": os.environ.get(ENV_AGENT),
    }


def response_text(payload: dict) -> str:
    """판정 대상 응답 전문을 얻는다.

    **입력 선택이 이 게이트의 정확성을 지배한다.** 실측(2026-07-29T11:25):
    Stop 훅 입력에 `last_assistant_message` 가 문자열로 최종 응답 전문을 담고,
    같은 시점의 전사 파일에는 최종 응답이 **아직 없다**(전사 4줄, 최종 응답 부재).

    전사를 읽으면 직전 턴의 산문을 판정하게 되고, 머리 표기가 있는 응답이
    차단된다 — 실제로 그 오차단이 났다. 오차단은 시정 경로를 봉쇄하는
    최악형이므로(S§9.3-14) 입력은 플랫폼이 주는 값을 1순위로 쓴다.

    둘 다 없으면 빈 문자열을 돌려준다 — 호출부는 이것을 '위반'이 아니라
    '판정 불가'로 다뤄야 한다(침묵과 무위반의 구별 — S§7).
    """
    lam = payload.get("last_assistant_message")
    if isinstance(lam, str) and lam.strip():
        return lam
    if isinstance(lam, dict):
        content = lam.get("content") or []
        joined = "".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
        if joined.strip():
            return joined
    tp = payload.get("transcript_path")
    if tp and pathlib.Path(tp).exists():
        try:
            for ln in reversed(pathlib.Path(tp).read_text(
                    encoding="utf-8").splitlines()):
                rec = json.loads(ln)
                if rec.get("type") != "assistant":
                    continue
                content = rec.get("message", {}).get("content", [])
                txt = "".join(b.get("text", "") for b in content
                              if isinstance(b, dict) and b.get("type") == "text")
                if txt.strip():
                    return txt
        except (OSError, json.JSONDecodeError):
            return ""
    return ""


def emit(obj: dict | None = None) -> None:
    print(json.dumps(obj or {}, ensure_ascii=False))


def deny(reason: str, event: str = "PreToolUse") -> None:
    emit({"hookSpecificOutput": {
        "hookEventName": event,
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }})


def block_stop(reason: str) -> None:
    """Stop 훅의 차단 반환 — 사유와 함께 재프롬프트로 이어진다."""
    emit({"decision": "block", "reason": reason})


def safe(fn):
    """기록 목적 훅은 fail-open 이다(D§1.2). 훅 내부 오류가 세션을 죽이면
    기록 장치가 실행 장치를 파괴한다."""
    def wrapper():
        try:
            return fn()
        except Exception as exc:                  # noqa: BLE001
            try:
                print(f"[hook-error] {fn.__name__}: {exc}", file=sys.stderr)
            except Exception:
                pass
            emit({})
            return 0
    return wrapper


# ── A§5.4-② docs/ 하위 직접 쓰기 차단 (fail-closed) ─────────────
_DOC_PATH_RE = re.compile(r"(^|/)docs/")


def judge_docs_write(payload: dict) -> str | None:
    """차단 사유 문자열 또는 None.

    실측(E§9-M3, 2026-07-29): PreToolUse 의 deny 반환이 Write 호출을 실제로
    차단하는 것을 확인했다 — 이 계층은 관측형이 아니라 차단형이다.
    """
    ti = payload.get("tool_input") or {}
    target = str(ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or "")
    if not target:
        return None
    if _DOC_PATH_RE.search(target.replace("\\", "/")):
        return ("docs/ 하위는 직접 쓰기 금지입니다 — 생성·착지 도구"
                "(harness/tools/land.py)를 쓰십시오. 원자 쓰기·추적 등재·인덱스"
                "·감사 이벤트가 한 단위여야 하고, 직접 쓰기는 그 단위를 깹니다.")
    return None


# ── A§5.4-③ 셸 리다이렉션 관측 (관측형 — 승격 조건 명문화) ──────
_REDIR_RE = re.compile(r"(>>?|tee)\s+[\"']?\S*docs/")


def observe_shell_write(payload: dict) -> str | None:
    """관측형이다. 문자열 판정은 원리적으로 불완전하므로(미탐 실측 6/12)
    차단으로 시작하지 않는다.

    승격 조건(도입 시점 명문화 — S§9.3-15): 누적 관측 30건 이상에서 오탐률
    10% 미만이면 차단 승격, 50% 초과면 규칙 삭제.
    """
    ti = payload.get("tool_input") or {}
    cmd = str(ti.get("command") or "")
    if "land.py" in cmd:
        return None                     # 생성 도구 자기 호출은 화이트리스트
    return cmd if _REDIR_RE.search(cmd) else None


# ── X8 응답 머리 표기 게이트 (F§3.2 확장) ───────────────────────
_GATE_FENCE_RE = re.compile(r"```lead-gate\s*\n(.*?)```", re.S)
HEAD_RE = re.compile(r"^\s*\[\s*([0-9T:\-+.Z]{19,32})\s*·\s*([^·\]]+?)\s*·\s*"
                     r"([a-z][a-z0-9-]*)\s*\]")


# 드리프트 대조의 **기준점**. 이 값이 바뀌면 판정이 통째로 바뀐다.
#
# 초안은 기준점을 `턴 시작 시각`으로 뒀다가 오차단을 냈다(실사고 2026-07-29T03:26
# — 19분짜리 턴에서 정직하게 잰 머리 시각이 1,160초 벌어져 차단됐다).
# 두 값이 같아야 할 이유가 애초에 없는 짝이었다: 응답 대기 무제한이 정책인 한
# (S§4.4) 턴 길이는 상한을 가정할 수 없고, B§3.6 이 고아 판정에서 같은 함정을
# 이미 기록했다("스텝 예상 시간 자체를 정할 수 없다").
#
# 기준점을 **Stop 시점의 실측 시각**으로 바꾸면 대조가 같은 짝이 된다 —
# 응답 직전에 잰 값은 Stop 과 초 단위로 가깝고, 옛 값을 재사용하면 벌어진다.
DRIFT_REFERENCE = "stop-time"


def check_head(text: str, *, registry_names: set[str], role_keys: set[str],
               drift_cap_s: int, now=None, turn_started_at=None,
               injected_at=None) -> tuple[bool, str]:
    """머리 표기 3요소의 구조 검사 + 시각 드리프트 대조.

    구조 존재와 시각 대조는 결정론이므로 즉시 차단형이다(F§10.2 1·3행).
    존댓말은 여기서 판정하지 않는다 — 그 축은 관측형이다.

    `turn_started_at` 은 판정에 쓰지 않고 **기록에만** 남긴다. 판정 기준은
    `now`(Stop 시점 실측)이다 — 위 DRIFT_REFERENCE 주석의 근거.
    """
    if now is None:
        now = injected_at        # 하위 호환: 호출부가 아직 안 넘기는 경우
    m = HEAD_RE.search(text or "")
    if not m:
        return False, ("응답 머리 표기가 없습니다. 첫 줄에 "
                       "[측정 시각 · 표시명 · 역할 키] 3요소를 출력하십시오"
                       "(S§2-20).")
    at, name, role = m.group(1), m.group(2).strip(), m.group(3)
    if role not in role_keys:
        return False, f"역할 키가 로스터 밖입니다: {role!r}"
    if registry_names and name not in registry_names:
        return False, (f"표시명이 이름 레지스트리에 없습니다: {name!r} — "
                       f"역할 키 단독 표기는 금지입니다")
    try:
        d = clock.drift_seconds(at, now)
    except ValueError:
        return False, f"머리 표기 시각이 ISO 8601 초 단위가 아닙니다: {at!r}"
    if d is None:
        # 대조 불가는 통과가 아니라 '대조 불가'다. 구조는 통과시키되 사실을 남긴다.
        return True, "시각 대조 불가 — 기준 시각 부재(침묵과 무위반의 구별)"
    if d > drift_cap_s:
        elapsed = ""
        if turn_started_at is not None:
            try:
                secs = int((clock.parse_iso(at) - turn_started_at).total_seconds())
                elapsed = f" (참고: 턴 시작 이후 {secs}초 경과 — 이 값은 판정에 쓰지 않습니다)"
            except ValueError:
                elapsed = ""
        return False, (f"머리 표기 시각이 응답 종료 시점 실측값과 {int(d)}초 "
                       f"벌어졌습니다 (상한 {drift_cap_s}초){elapsed}. "
                       f"응답 직전에 다시 재십시오.")
    return True, ""


def extract_gate_block(text: str) -> dict | None:
    m = None
    for m in _GATE_FENCE_RE.finditer(text or ""):
        pass                      # 마지막 블록이 정본(1응답 1블록)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"_parse_error": True}


def scan_praise(obj, lexicon) -> str | None:
    """F§7.9 ⑥-b′ — 닫힌 어휘 × 닫힌 대상 필드. 자유 산문은 대상이 아니다."""
    targets: list[str] = []
    if not isinstance(obj, dict):
        return None
    for c in obj.get("claims", []) or []:
        if isinstance(c, dict):
            targets.append(str(c.get("statement", "")))
            rep = c.get("report") or {}
            if isinstance(rep, dict):
                for a in rep.get("allowed", []) or []:
                    if isinstance(a, dict):
                        targets.append(str(a.get("statement", "")))
                targets += [str(x) for x in (rep.get("disallowed") or [])]
                for ln in rep.get("chain", []) or []:
                    if isinstance(ln, dict):
                        targets.append(str(ln.get("statement", "")))
    for t in targets:
        for w in lexicon:
            if w and w in t:
                return w
    return None


HONORIFIC_ENDINGS = ("습니다", "습니까", "십시오", "세요", "요.", "요!", "요?",
                     "ㅂ니다", "입니다", "합니다", "됩니다", "니다")


def observe_honorific(text: str) -> dict:
    """관측형 계층. 판정하지 않고 계수만 한다.

    차단형으로 걸지 않는 근거: 한국어 종결어미 판정은 인용·코드 펜스·표에서
    오탐이 구조적으로 발생하고, 진실 축에 차단형 장치를 세우지 않는다는
    규정([R22])에 걸린다. 승격 조건은 정책 파일에 있다.
    """
    body = re.sub(r"```.*?```", "", text or "", flags=re.S)
    body = re.sub(r"^\s*[|>].*$", "", body, flags=re.M)
    # 길이 하한을 3으로 둔다. 8로 두면 "완료했습니다" 같은 짧은 문장이 통째로
    # 빠지는데, 그런 문장이야말로 이 관측이 세려는 대상이다 — 하한이 대상을
    # 삼키면 계수는 0에 수렴하고 관측 자체가 무의미해진다.
    sents = [s.strip() for s in re.split(r"[.!?\n]+", body) if len(s.strip()) > 2]
    ko = [s for s in sents if re.search(r"[가-힣]", s)]
    polite = [s for s in ko if s.rstrip().endswith(HONORIFIC_ENDINGS)]
    return {"korean_sentences": len(ko), "polite": len(polite),
            "ratio": round(len(polite) / len(ko), 3) if ko else None}
