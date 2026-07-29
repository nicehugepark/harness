"""정본: C§7.2 이름 레지스트리 파일 스키마 · C§7.3 설치 시 질문 흐름.

단일 파일·단일 라이터·원자 쓰기·append-only 이력. 설치 필수물이다 —
레지스트리가 없는 배포에서는 이름 해석이 통째로 죽는다.

표시명 변경 시 과거 문서를 소급 수정하지 않는다. 문서의 표시명은 작성 시점
값이고 `history` 가 시점 매핑을 제공한다.
"""
from __future__ import annotations

import os
import pathlib

import yaml

from . import clock, roster

REL = "config/names/name-registry.yaml"
SCHEMA_VERSION = 1


def path(root_dir) -> pathlib.Path:
    return pathlib.Path(root_dir) / REL


def load(root_dir) -> dict:
    p = path(root_dir)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "updated": None,
                "agents": [], "history": []}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"schema_version": SCHEMA_VERSION, "updated": None,
                "agents": [], "history": [], "_load_error": True}
    data.setdefault("agents", [])
    data.setdefault("history", [])
    return data


def display_names(root_dir) -> set[str]:
    return {a.get("display_name") for a in load(root_dir).get("agents", [])
            if a.get("display_name")}


def by_role(root_dir) -> dict[str, str]:
    return {a["role_key"]: a["display_name"]
            for a in load(root_dir).get("agents", [])
            if a.get("role_key") and a.get("display_name")}


def resolve(root_dir, name: str) -> str | None:
    """별칭·표시명 → role_key. 리드가 사람 발화를 해석할 때 쓴다(C§8.1-11).

    해석기 프로그램을 따로 만들지 않는다 — 레지스트리 조회 한 번이면 된다는
    것이 통신 평면 판정의 결론이다.
    """
    for a in load(root_dir).get("agents", []):
        if name == a.get("display_name") or name in (a.get("aliases") or []):
            return a.get("role_key")
        if name == a.get("role_key"):
            return a.get("role_key")
    return None


def valid(reg: dict) -> tuple[bool, str]:
    """C§7.2 유일성 불변식 — role_key 유일 · display_name 전역 유일 ·
    alias 는 어떤 display_name·role_key·타 alias 와도 충돌 금지."""
    agents = reg.get("agents", [])
    keys = [a.get("role_key") for a in agents]
    names = [a.get("display_name") for a in agents]
    aliases = [al for a in agents for al in (a.get("aliases") or [])]
    if len(keys) != len(set(keys)):
        return False, "role_key 중복"
    if len(names) != len(set(names)):
        return False, "display_name 중복"
    pool = set(keys) | set(names)
    for al in aliases:
        if al in pool:
            return False, f"별칭이 표시명·역할 키와 충돌: {al!r}"
        if aliases.count(al) != 1:
            return False, f"별칭 중복: {al!r}"
    return True, ""


def save(root_dir, reg: dict) -> None:
    """원자 쓰기 — valid() 를 통과하지 못하면 rename 하지 않는다(착지 거부)."""
    ok, why = valid(reg)
    if not ok:
        raise ValueError(f"레지스트리 불변식 위반: {why}")
    reg["schema_version"] = SCHEMA_VERSION
    reg["updated"] = clock.iso_local()
    p = path(root_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(reg, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def missing_roles(root_dir) -> list[str]:
    """C§7.3 멱등 — 이미 등록된 role_key 는 건너뛴다. 재질문 금지의 물적 기반."""
    have = {a.get("role_key") for a in load(root_dir).get("agents", [])}
    return [k for k in roster.ROLE_KEYS if k not in have]


def register(root_dir, role_key: str, display_name: str,
             aliases: list[str] | None = None, actor: str = "installer") -> dict:
    reg = load(root_dir)
    now = clock.iso_local()
    existing = next((a for a in reg["agents"] if a.get("role_key") == role_key), None)
    if existing:
        old = existing.get("display_name")
        existing["display_name"] = display_name
        existing["aliases"] = aliases or existing.get("aliases") or []
        existing["updated"] = now
        reg["history"].append({"ts": now, "role_key": role_key,
                               "field": "display_name", "old": old,
                               "new": display_name, "actor": actor})
    else:
        reg["agents"].append({
            "role_key": role_key, "display_name": display_name,
            "aliases": aliases or [], "created": now, "updated": now,
        })
        reg["history"].append({"ts": now, "role_key": role_key,
                               "field": "display_name", "old": None,
                               "new": display_name, "actor": actor})
    save(root_dir, reg)
    return reg
