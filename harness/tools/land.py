#!/usr/bin/env python3
"""생성·착지 도구 — 커스텀 잔량 #1 (A§5.4-①).

이 실행체가 land() 전체를 한 프로세스의 한 단위로 수행한다: 검증(E01~E17)
→ 원자 쓰기 → 추적 등재(git add / 미러+대장) → 격리 → 인덱스 append
→ 감사 이벤트. 훅 표면으로는 원리적으로 불가능하다 — `PreToolUse` 는 도구
실행 전 허용/거부만 할 수 있고 실제 쓰기는 그 도구가 하기 때문이다(비원자).

**본문은 셸을 경유하지 않는다**(R9): `--body-file` 또는 표준입력만 받는다.
문자열 인자 자체가 없다 — 셸이 본문을 해석해 지침이 잘려나간 사고의 원천 차단.

사용
  land.py --type DS --title "제목" --visibility public \\
          --what "무슨 일" --why "왜" --tags stage/design,origin/agent \\
          --body-file draft.md [--stage design] [--refs request=RQ-...]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "harness" / "lib"))

from harness_core import (clock, envelope, frontmatter, gate, ids,  # noqa: E402
                          paths, policy, registry, schema)


def repo_root_id(root: pathlib.Path) -> str:
    """X5 — root := hex(SHA-256(저장소 최초 커밋 해시))[0:12] | "bootstrap"."""
    import hashlib
    if not (root / ".git").exists():
        return gate.BOOTSTRAP_ROOT
    r = subprocess.run(["git", "-C", str(root), "rev-list", "--max-parents=0", "HEAD"],
                       capture_output=True, text=True)
    first = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not first:
        return gate.BOOTSTRAP_ROOT
    return hashlib.sha256(first[-1].encode()).hexdigest()[:12]


def build_identity(root: pathlib.Path, role_key: str) -> gate.Identity:
    import platform
    machine = os.environ.get("HARNESS_MACHINE") or platform.node()
    session = os.environ.get("HARNESS_SESSION") or "manual"
    display = registry.by_role(root).get(role_key) or os.environ.get(
        "HARNESS_AGENT") or role_key
    return gate.Identity(machine=machine, session=session, author=display,
                         role_key=role_key, root=repo_root_id(root))


def append_index(root: pathlib.Path, meta: dict, rel) -> None:
    """A§7.3 — 착지 시 태그당 1행 + docs.jsonl 1행 append. O(1).

    이벤트당 전량 재생성 금지(S§10-1)와 append 즉시성의 양립점이다:
    상태 변경도 append 이고 읽기는 id 당 최후 레코드 승리, 컴팩션은 배치.
    """
    rec = {"id": meta["id"], "path": rel.as_posix(), "title": meta.get("title"),
           "what": meta.get("what"), "state": schema.read_state(meta),
           "created": meta.get("created"), "updated": meta.get("updated"),
           "tags": meta.get("tags", [])}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    targets = [root / paths.docs_index_path()]
    for t in meta.get("tags", []):
        axis, _, value = str(t).partition("/")
        if value:
            targets.append(root / paths.tag_index_path(axis, value))
    for p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="문서 생성·착지 (A§5.2 land)")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--type", required=True, choices=sorted(ids.EXT_BY_TYPE))
    ap.add_argument("--title", required=True)
    ap.add_argument("--visibility", required=True, choices=["public", "private"],
                    help="기본값 없음 — 미지정은 존재하지 않는 값이다(A§1.5)")
    ap.add_argument("--what", required=True)
    ap.add_argument("--why", required=True)
    ap.add_argument("--tags", required=True,
                    help="쉼표 구분. 자동 파생 축(stage/·origin/)은 도구가 주입한다")
    ap.add_argument("--role", default=os.environ.get("HARNESS_ROLE", "lead"))
    ap.add_argument("--requester", default="의사결정권자")
    ap.add_argument("--stage", default=None, choices=schema.DS_STAGES + [None])
    ap.add_argument("--state", default=None)
    ap.add_argument("--refs", action="append", default=[],
                    help="slot=ID 형식, 반복 가능")
    ap.add_argument("--extra-json", default=None,
                    help="유형별 추가 필드 JSON 파일 경로(셸 비경유)")
    ap.add_argument("--body-file", default=None,
                    help="본문 파일. 생략하면 표준입력을 읽는다")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    body = (pathlib.Path(args.body_file).read_text(encoding="utf-8")
            if args.body_file else sys.stdin.read())
    if not body.strip():
        print("본문이 비었다 — 파일 또는 표준입력으로 넘겨라", file=sys.stderr)
        return 1

    ident = build_identity(root, args.role)
    doc_id = ids.mint(args.type, machine_id=ident.machine, session_id=ident.session)
    now = clock.iso_local()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    # R12 — 자동 파생 가능한 축은 사람이 입력하지 않는다.
    tags = [t for t in tags if not t.startswith(("stage/", "origin/"))]
    if args.stage:
        tags.append(f"stage/{args.stage}")
    tags.append("origin/agent" if args.role != "lead" or os.environ.get(
        "HARNESS_PLANE") else "origin/human")

    default_state = {"RQ": "received", "AJ": "open"}.get(args.type, "active")
    meta = {
        "schema": schema.CURRENT_SCHEMA,
        "id": doc_id, "type": args.type, "title": args.title,
        "visibility": args.visibility, "state": args.state or default_state,
        "root": ident.root, "created": now, "updated": now,
        "machine": ident.machine, "session": ident.session,
        "author": ident.author, "requester": args.requester,
        "what": args.what, "why": args.why, "tags": sorted(set(tags)),
        "refs": {},
    }
    if args.stage:
        meta["stage"] = args.stage
    for r in args.refs:
        slot, _, val = r.partition("=")
        if slot and val:
            meta["refs"].setdefault(slot, val)
    if args.extra_json:
        meta.update(json.loads(pathlib.Path(args.extra_json).read_text("utf-8")))

    text = frontmatter.render(meta, body)
    if args.dry_run:
        sys.stdout.write(text)
        return 0

    res = gate.land(text, root_dir=root, identity=ident,
                    visibility=args.visibility)
    if not res.ok:
        print(f"착지 거부 [{res.code}] {gate.ERROR_CLASSES.get(res.code, '')}: "
              f"{res.detail}", file=sys.stderr)
        return 1

    append_index(root, res.meta, res.path)
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events",
                            identity={"machine": ident.machine,
                                      "session": ident.session,
                                      "plane": os.environ.get("HARNESS_PLANE",
                                                              "interactive"),
                                      "req": os.environ.get("HARNESS_REQ"),
                                      "role": ident.role_key,
                                      "agent": ident.author})
        w.append("doc.landed", {"id": res.meta["id"], "path": res.path.as_posix()})
    except Exception as exc:                      # noqa: BLE001
        print(f"[warn] 감사 이벤트 실패(착지는 완료): {exc}", file=sys.stderr)

    print(res.path.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
