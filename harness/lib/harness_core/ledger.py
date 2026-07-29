"""정본: B§1.2 `session_ref` · B§1.3 전이 5 · B§3.6 lease fencing · A§4.1 자동 필드.

**요청 문서의 `state` 를 쓰는 유일 경로**다. 이 모듈 밖에서 상태를 쓰는 자리가
없어야 "전이는 전이표의 행으로만"이 성립한다.

lease(`session_ref`)는 스케줄러 전용 기입이다. 그 기입이 곧 점유 선언이고,
착지 게이트가 발신 세션 신원을 이 값과 대조해 불일치를 거부한다 — 이중 세션의
쓰기·머지가 기록 평면에 도달할 수 없게 하는 장치다.

**claim 은 세션을 띄우기 전에 한다.** 띄우고 나서 claim 하면 그 사이의 크래시가
고아 세션을 남기고, 다음 틱이 같은 요청을 또 띄운다 — 실사고 2026-07-29T04:33
에서 같은 요청에 세션 3개가 떴다.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from . import clock, frontmatter, schema

# 계약 상수 — 순서가 바뀌면 고아 세션이 생긴다. 시험이 이 값을 고정한다.
CLAIM_BEFORE_SPAWN = True


def can_claim(meta: dict) -> tuple[bool, str]:
    """전이 5 의 가드를 원장 관점에서 본 것.

    이미 실행 상태이거나 다른 lease 가 붙어 있으면 claim 하지 않는다 —
    "다음 하나 고르기"가 아니라 "점유 가능한가"의 판정이다.
    """
    state = schema.read_state(meta)
    if state != "queued":
        return False, f"이미 {state} 상태다 — queued 만 claim 대상이다"
    if meta.get("session_ref"):
        return False, (f"이미 lease 가 붙어 있다: {meta['session_ref']!r} — "
                       f"고아 회수는 종료 실증 인터록이 판정한다")
    return True, ""


def lease_ok(meta: dict, session_ref: str) -> bool:
    """B§3.6 fencing — 이 요청에 대한 유효 세션은 원장의 `session_ref` 단 하나다."""
    cur = meta.get("session_ref")
    return bool(cur) and cur == session_ref


def claim(root, rel_path: str, session_ref: str, *, machine: str) -> tuple[bool, str]:
    """queued → designing 전이 + lease 기입을 **원자적으로** 한다.

    쓰기는 임시 파일 + rename 이고, 추적 등재까지가 한 단위다. 실패하면
    아무것도 바꾸지 않는다 — 부분 상태를 남기면 다음 틱의 판정이 오염된다.
    """
    root = pathlib.Path(root)
    p = root / rel_path
    try:
        doc = frontmatter.parse(p.read_text(encoding="utf-8"))
    except (OSError, frontmatter.ParseError) as exc:
        return False, f"원장 읽기 실패: {exc}"

    ok, why = can_claim(doc.meta)
    if not ok:
        return False, why

    meta = dict(doc.meta)
    key = "status" if int(meta.get("schema", 2)) <= 1 else "state"
    meta[key] = "designing"
    meta["session_ref"] = session_ref
    meta["machine_assigned"] = machine
    meta["updated"] = clock.iso_local()

    payload = frontmatter.render(meta, doc.body)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False, f"원자 쓰기 실패: {exc}"

    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", rel_path],
                       capture_output=True, text=True)
    return True, session_ref


def release(root, rel_path: str, session_ref: str, *, to_state: str) -> tuple[bool, str]:
    """세션 종결 시 lease 를 놓는다. lease 보유자만 놓을 수 있다."""
    root = pathlib.Path(root)
    p = root / rel_path
    try:
        doc = frontmatter.parse(p.read_text(encoding="utf-8"))
    except (OSError, frontmatter.ParseError) as exc:
        return False, str(exc)
    if not lease_ok(doc.meta, session_ref):
        return False, ("lease 보유자가 아니다 — 이 세션의 쓰기는 fencing 대상이다")
    meta = dict(doc.meta)
    key = "status" if int(meta.get("schema", 2)) <= 1 else "state"
    meta[key] = to_state
    meta["updated"] = clock.iso_local()
    if to_state in ("done", "failed", "void"):
        meta["session_ref"] = None
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(frontmatter.render(meta, doc.body))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", rel_path],
                       capture_output=True, text=True)
    return True, to_state
