"""정본: A§5.2 착지 게이트 판정식과 오류 코드표 · A§5.4 집행 호스트 분리.

호스트는 이 커스텀 도구다. 플랫폼 훅은 우회 차단·관측 전용이다 —
`PreToolUse` 는 도구 실행 전 허용/거부만 할 수 있고 실제 쓰기는 그 도구가
수행하므로(비원자), land() 의 원자 쓰기·추적 확인·격리를 훅이 대행할 수 없다.

판정은 전부 거부형이다. 경고 반환 분기가 이 모듈에 존재하지 않는다.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import shutil
import subprocess

from . import clock, frontmatter, ids, paths, policy, schema

# ── A§5.2 오류 코드표 ────────────────────────────────────────────
# E09 는 결번이다(X2-e). 원자료에도 같은 결번이고 사유 기록이 없다 —
# 번호를 재사용하면 과거 판정 기록의 코드 해석이 판별 불가능해진다.
ERROR_CLASSES = {
    "E01": "frontmatter 파싱 불능",
    "E02": "스키마 판 미지원",
    "E03": "메타 결손·신원 불일치",
    "E04": "ID·파일명 문법 위반",
    "E05": "경로-메타 불일치",
    "E06": "요약 부재·유형별 필수 절 결손·규율 슬롯 린트 불통과",
    "E07": "참조 대상 부재",
    "E08": "참조 유형 불일치",
    "E10": "카테고리 파일 형식 계약 위반",
    "E11": "비텍스트 파일",
    "E12": "자격증명 패턴 포함",
    "E13": "ID 중복(재시도 소진)",
    "E14": "추적 등재 실패",
    "E15": "private 동기 보존 실패(미러·대장)",
    "E16": "상찬 어휘 검출(닫힌 어휘 × 닫힌 대상 필드)",   # X2-d
    "E17": "루트 불일치",                                    # X5-c
}

# X2 — 린트 유형 하한. 내용 트리거와 OR 로 겹친다.
LINT_TYPE_FLOOR = {"DS", "DN", "RF"}
_R_ITEM_RE = re.compile(r"^\s*-\s\[R\d+\]", re.M)

# A§5.2 E12 — 자격증명 패턴. 값이 아니라 패턴 클래스로 적는다
# (값을 적으면 검출기 자신이 유출물이 된다 — S§9.3-16).
_CRED_PATTERNS = [
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*"
               r"['\"]?[^\s'\"]{8,}"),
]

BOOTSTRAP_ROOT = "bootstrap"


@dataclasses.dataclass(frozen=True)
class Identity:
    """S§7 — 세션 생성 시점에 발급된 신원. 에이전트가 정하지 않는다."""
    machine: str
    session: str
    author: str
    role_key: str
    root: str


@dataclasses.dataclass
class Result:
    ok: bool
    code: str | None = None
    detail: str = ""
    path: pathlib.PurePosixPath | None = None
    meta: dict | None = None

    def __bool__(self):
        return self.ok


def _reject(code, detail=""):
    return Result(False, code, detail)


# ── 경로·형식 검사 (E10·E11) ─────────────────────────────────────
def check_docs_path(rel_path: str) -> str | None:
    p = pathlib.PurePosixPath(rel_path)
    if p.parts[:1] != ("docs",):
        return None
    ext = p.suffix.lstrip(".")
    if ext not in ids.ALLOWED_EXT:
        return "E11"
    category = p.parts[1] if len(p.parts) > 1 else ""
    stem_type = p.name.split("-", 1)[0]
    expected_ext = ids.EXT_BY_TYPE.get(stem_type)
    if expected_ext is None:
        # 유형 코드를 읽을 수 없는 파일명 — 카테고리 계약으로 판정
        cat_exts = {t for t, c in paths.CATEGORY_BY_TYPE.items() if c == category}
        if not any(ids.EXT_BY_TYPE[t] == ext for t in cat_exts):
            return "E10"
        return None
    if expected_ext != ext:
        return "E10"
    if paths.CATEGORY_BY_TYPE.get(stem_type) != category:
        return "E10"
    return None


# ── X2 규율 슬롯 린트 ────────────────────────────────────────────
def lint_target(meta: dict, body: str) -> bool:
    """린트_적용대상 := type ∈ {DS,DN,RF} ∨ 본문에 `- [R n]` 매치 ≥ 1건."""
    if meta.get("type") in LINT_TYPE_FLOOR:
        return True
    return bool(_R_ITEM_RE.search(body or ""))


def lint_slots(meta: dict, doc: frontmatter.Document) -> tuple[bool, str]:
    if not lint_target(meta, doc.body):
        return True, ""
    items = doc.discipline_items()
    if not items:
        if meta.get("type") in LINT_TYPE_FLOOR and "규율 항목 없음" not in doc.body:
            return False, "슬롯대상부재선언없음"
        return True, ""
    for it in items:
        num = re.search(r"\[R(\d+)\]", it)
        label = f"R{num.group(1)}" if num else "R?"
        has_dev = "[장치 — " in it
        has_non = "장치 불가 — " in it
        if not (has_dev or has_non):
            return False, f"슬롯부재 {label}"
        for tag, code in (("[장치 — ", "장치서술공백"),
                          ("장치 불가 — ", "불가근거공백")):
            if tag in it:
                seg = it.split(tag, 1)[1]
                seg = re.split(r"\s*\|\s*|\]\s*$", seg)[0].strip()
                if not seg:
                    return False, f"{code} {label}"
    return True, ""


# ── X2-d 아부 금지 (E16) ─────────────────────────────────────────
def _praise_targets(meta: dict, doc: frontmatter.Document) -> list[str]:
    """닫힌 대상 필드만 수집한다. 이 목록 밖은 읽지 않는다(F§7.9)."""
    out: list[str] = []
    for blk in doc.yaml_blocks():
        for item in blk.get("criteria", []) or []:
            if isinstance(item, dict):
                out += [str(item.get("what", "")), str(item.get("pass", ""))]
    summary = doc.section("요약") or ""
    for m in re.finditer(r"\*\*(?:확정|미결|실측 대기)\*\*\s*—\s*(.+)", summary):
        out.append(m.group(1))
    return [s for s in out if s]


def check_praise(meta, doc, lexicon) -> str | None:
    for field_text in _praise_targets(meta, doc):
        for word in lexicon:
            if word and word in field_text:
                return word
    return None


# ── land() — A§5.2 ───────────────────────────────────────────────
def land(doc_bytes: str, *, root_dir, identity: Identity, visibility: str,
         slug: str = "", pol: dict | None = None) -> Result:
    root_dir = pathlib.Path(root_dir)
    pol = pol if pol is not None else policy.load(root_dir)

    # E01
    try:
        doc = frontmatter.parse(doc_bytes)
    except frontmatter.ParseError as exc:
        return _reject("E01", str(exc))
    fm = doc.meta

    # E02
    ver = fm.get("schema")
    if not isinstance(ver, int) or ver not in schema.SUPPORTED_SCHEMAS:
        return _reject("E02", f"schema={ver!r}")

    # E03 — 폐기 키 / 공통 결손 / 신원 대조
    retired = schema.retired_keys_present(fm)
    if retired:
        return _reject("E03", f"폐기 키 존재(X7): {retired}")
    common_missing = [f for f in schema.missing_required(fm)
                      if f in schema.COMMON_REQUIRED or f in ("status",)]
    if common_missing:
        return _reject("E03", f"결손 필드: {common_missing}")
    if (fm.get("author") != identity.author
            or fm.get("machine") != identity.machine
            or fm.get("session") != identity.session):
        return _reject("E03", "신원 불일치")

    # E17 — 루트 대조 (X5)
    if ver >= 2:
        if fm.get("root") != identity.root:
            return _reject("E17", f"root={fm.get('root')!r} ≠ {identity.root!r}")

    # E04 — ID 문법 + 유형 교차.
    # 설계 의사코드(A§5.2)는 유형별 필수 필드까지 검사한 뒤 E04 를 본다. 여기서는
    # 공통 필드만 먼저 보고 E04 를 앞으로 당긴다 — `type` 이 틀린 문서에서
    # 유형별 필수 목록을 계산하면 적용되지도 않는 필드 결손을 보고하게 되고,
    # 그것이 구현자를 잘못된 자리로 보낸다. 거부 여부는 두 순서에서 동일하고
    # 보고되는 코드만 달라진다(어느 문서도 한 순서에서 통과하고 다른 순서에서
    # 거부되지 않는다).
    try:
        parsed = ids.parse(str(fm.get("id")))
    except ValueError as exc:
        return _reject("E04", str(exc))
    if parsed.type_code != fm.get("type"):
        return _reject("E04", "id 유형과 type 필드 불일치")

    # E03 — 유형별 필수 필드(유형이 검증된 뒤에만 의미가 있다)
    type_missing = [f for f in schema.missing_required(fm)
                    if f not in schema.COMMON_REQUIRED]
    if type_missing:
        return _reject("E03", f"유형별 결손 필드: {type_missing}")
    if not schema.state_valid(fm):
        return _reject("E03", f"state 값이 enum 밖: {schema.read_state(fm)!r}")

    # E05 — 경로-메타 대조
    if fm.get("visibility") != visibility:
        return _reject("E05", "착지 지시 가시성과 frontmatter 불일치")
    try:
        rel = paths.doc_path(parsed.raw, visibility, slug or fm.get("title", ""))
    except ValueError as exc:
        return _reject("E05", str(exc))

    # E10 · E11
    fmt = check_docs_path(rel.as_posix())
    if fmt:
        return _reject(fmt, rel.as_posix())

    # E06 — 필수 절 + 검증 기준 3요소 + 규율 슬롯 린트
    for sec in schema.required_sections(fm):
        if not doc.has_nonempty_section(sec):
            return _reject("E06", f"필수 절 결손·공백: {sec}")
    if fm.get("type") == "DS" and fm.get("stage") == "design":
        blocks = [b for b in doc.yaml_blocks() if "criteria" in b]
        if not blocks:
            return _reject("E06", "검증 기준 기계 판독 블록 부재")
        items = blocks[0].get("criteria") or []
        if not items:
            return _reject("E06", "검증 기준 항목 0건")
        for it in items:
            if not isinstance(it, dict) or not all(
                    str(it.get(k, "")).strip() for k in ("what", "how", "pass")):
                return _reject("E06", "검증 기준 3요소 결손")
    ok, why = lint_slots(fm, doc)
    if not ok:
        return _reject("E06", f"규율 슬롯 린트: {why}")

    # X1 — stage=dev 증거 택일
    if fm.get("type") == "DS" and fm.get("stage") == "dev":
        keys = [k for k in schema.EVIDENCE_KEY.values() if fm.get(k)]
        if not keys:
            return _reject("E03", "stage=dev 는 tdd_evidence 또는 doc_evidence 필수(X1)")
        for kind, key in schema.EVIDENCE_KEY.items():
            if fm.get(key):
                blk = fm[key] or {}
                lack = [f for f in schema.EVIDENCE_FIELDS[kind]
                        if not str(blk.get(f, "")).strip()]
                if lack:
                    return _reject("E03", f"{key} 결손 필드: {lack}")

    # E07 · E08 — 참조 무결성
    for slot, val in (fm.get("refs") or {}).items():
        targets = val if isinstance(val, list) else [val]
        for ref in targets:
            if not ref:
                continue
            try:
                rtype = ids.type_of(str(ref))
            except ValueError:
                return _reject("E07", f"참조 ID 문법 위반: {slot}={ref}")
            expect = schema.REF_SLOT_TYPES.get(slot, None)
            if expect and rtype != expect:
                return _reject("E08", f"{slot} 은 {expect} 를 기대한다: {ref}")
            found = any((root_dir / c.parent).exists() and
                        list((root_dir / c.parent).glob(f"{ref}*"))
                        for c in paths.candidate_paths(str(ref)))
            if not found:
                return _reject("E07", f"참조 대상 부재: {slot}={ref}")

    # A§7.2 — 태그 정규화(거부 아님)
    tags, moved = schema.normalize_tags(fm.get("tags"))
    if not tags:
        return _reject("E03", "tags 공집합")
    fm["tags"] = tags

    # E12 — 자격증명 패턴
    for pat in _CRED_PATTERNS:
        if pat.search(doc_bytes):
            return _reject("E12", "자격증명 패턴 — 값은 볼트로, 문서엔 경로 참조만")

    # E16 — 상찬 어휘 (X2-d)
    hit = check_praise(fm, doc, pol.get("praise_lexicon", []))
    if hit:
        return _reject("E16", f"상찬 어휘 적중: {hit!r}")

    # E13 — 로컬 중복
    shard = root_dir / rel.parent
    if shard.exists() and list(shard.glob(f"{parsed.raw}*")):
        return _reject("E13", f"샤드 내 동일 ID 존재: {parsed.raw}")

    # ── 원자 쓰기 ────────────────────────────────────────────────
    shard.mkdir(parents=True, exist_ok=True)
    final = root_dir / rel
    tmp = final.with_suffix(final.suffix + ".tmp")
    payload = frontmatter.render(fm, doc.body)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)

    # 추적 등재 — public 은 git, private 은 동기 미러 + 백업 대장
    if visibility == "public":
        code = _track_public(root_dir, rel)
        if code:
            _quarantine(root_dir, final, code)
            return _reject(code, "추적 등재 실패 — 격리")
    else:
        code = _mirror_private(root_dir, rel, pol)
        if code:
            _quarantine(root_dir, final, code)
            return _reject(code, "private 동기 보존 실패 — 격리")

    return Result(True, None, "landed", rel, fm)


def _git(root_dir, *args):
    return subprocess.run(["git", "-C", str(root_dir), *args],
                          capture_output=True, text=True)


def _track_public(root_dir, rel) -> str | None:
    if not (pathlib.Path(root_dir) / ".git").exists():
        return None          # 저장소 이전 단계(bootstrap) — 배치 잡이 흡수한다
    r = _git(root_dir, "add", "--", rel.as_posix())
    if r.returncode != 0:
        return "E14"
    chk = _git(root_dir, "ls-files", "--error-unmatch", "--", rel.as_posix())
    return None if chk.returncode == 0 else "E14"


def _mirror_private(root_dir, rel, pol) -> str | None:
    """A§5.2 private 분기 — 조용한 단일 로컬 사본 금지(E15)."""
    import hashlib
    backup_root = pathlib.Path(
        os.path.expanduser(pol.get("backup_root", "~/harness-backups"))
    )
    try:
        mirror = backup_root / "mirror" / rel
        mirror.parent.mkdir(parents=True, exist_ok=True)
        src = pathlib.Path(root_dir) / rel
        tmp = mirror.with_suffix(mirror.suffix + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, mirror)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        ledger = backup_root / "backup-registry.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        rec = json.dumps({
            "id": rel.name.split("-")[0] + "-" + rel.name.split("-")[1],
            "path": rel.as_posix(), "checksum": digest,
            "mirror_path": str(mirror), "at": clock.iso_utc(),
        }, ensure_ascii=False)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(rec + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        return "E15"
    return None


def _quarantine(root_dir, final: pathlib.Path, code: str):
    q = pathlib.Path(root_dir) / "derived" / "quarantine"
    q.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(final, q / f"{code}-{final.name}")
    except OSError:
        pass
