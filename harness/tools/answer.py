#!/usr/bin/env python3
"""상신 응답 도구 — 사람이 의사결정 문서에 답을 넣는 **유일 경로**.

정본: B§1.3 전이 17·18·19(사람 결정) · D§4 의사결정 문서 · B-5.

상신은 있는데 답을 넣는 도구가 없었다. 그러면 상신이 곧 영구 정지다 —
사람이 답할 의사가 있어도 답을 착지시킬 자리가 없기 때문이다.

  answer.py --adjudication AJ-... --answer "고른 것과 그 이유" --resume queued
  answer.py --list                        응답 대기 중인 상신을 보여준다

`--resume` 은 상신이 걸린 요청을 어디로 놓을지다: queued(재개) · failed(종결) ·
void(폐기). 생략하면 응답만 착지하고 요청은 hold 에 남는다 — 답과 재개는
다른 결정이라 하나로 묶지 않는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import clock, envelope, frontmatter, schema  # noqa: E402

RESUME_STATES = {"queued": "req.resumed", "failed": "req.closed",
                 "void": "req.voided"}


def find_open(root: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    out = []
    for f in sorted(glob.glob(str(root / "docs/adjudications/*/**/AJ-*.md"),
                              recursive=True)):
        p = pathlib.Path(f)
        try:
            m = frontmatter.parse(p.read_text(encoding="utf-8")).meta
        except Exception:                                  # noqa: BLE001
            continue
        if schema.read_state(m) in ("open", "notified"):
            out.append((p, m))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--adjudication")
    ap.add_argument("--answer", help="사람의 응답 문면")
    ap.add_argument("--answer-file", help="응답 문면 파일(셸 비경유)")
    ap.add_argument("--resume", choices=sorted(RESUME_STATES))
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    if args.list or not args.adjudication:
        rows = find_open(root)
        if not rows:
            print("응답 대기 중인 상신 0건")
            return 0
        for p, m in rows:
            print(f"{m['id']}  [{schema.read_state(m)}]  {m.get('title')}")
            print(f"  문항: {str(m.get('question'))[:200]}")
            print(f"  요청: {(m.get('refs') or {}).get('request')}")
            print(f"  경로: {p.relative_to(root)}")
        return 0

    hits = [t for t in find_open(root) if t[1]["id"] == args.adjudication] or [
        (pathlib.Path(f), frontmatter.parse(pathlib.Path(f).read_text("utf-8")).meta)
        for f in glob.glob(str(root / f"docs/adjudications/*/**/{args.adjudication}*.md"),
                           recursive=True)]
    if not hits:
        print(f"상신을 찾지 못했다: {args.adjudication}", file=sys.stderr)
        return 1
    p, meta = hits[0]

    body_ans = (pathlib.Path(args.answer_file).read_text(encoding="utf-8")
                if args.answer_file else args.answer)
    if not body_ans or not body_ans.strip():
        print("응답 문면이 비었다 — --answer 또는 --answer-file", file=sys.stderr)
        return 1

    doc = frontmatter.parse(p.read_text(encoding="utf-8"))
    m = dict(doc.meta)
    now = clock.iso_local()
    m["answered_at"] = now
    m["answered_by"] = "의사결정권자"
    m["state"] = "answered"
    m["updated"] = now
    block = (f"\n\n## 응답 ({now} · 의사결정권자)\n\n{body_ans.rstrip()}\n")
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(frontmatter.render(m, doc.body.rstrip() + block))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)
    rel = str(p.relative_to(root))
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", rel],
                       capture_output=True, text=True)
    print(f"응답 착지: {rel}")

    if not args.resume:
        print("요청은 hold 에 남는다 — 재개하려면 --resume 을 함께 준다")
        return 0

    req_id = (m.get("refs") or {}).get("request")
    cand = glob.glob(str(root / f"docs/requests/*/**/{req_id}*.md"), recursive=True)
    if not cand:
        print(f"[warn] 요청 문서를 찾지 못했다: {req_id}", file=sys.stderr)
        return 1
    rp = pathlib.Path(cand[0])
    rdoc = frontmatter.parse(rp.read_text(encoding="utf-8"))
    rm = dict(rdoc.meta)
    key = "status" if int(rm.get("schema", 2)) <= 1 else "state"
    if schema.read_state(rm) != "hold":
        print(f"요청이 hold 가 아니다({schema.read_state(rm)}) — 재개하지 않는다",
              file=sys.stderr)
        return 1
    rm[key] = args.resume
    rm["updated"] = now
    # 재개는 lease 없이 간다 — 그래야 스케줄러가 다시 집는다.
    rm["session_ref"] = None
    rm.setdefault("refs", {})
    rm["refs"] = {**(rm.get("refs") or {}), "adjudication": m["id"]}
    tmp = rp.with_suffix(rp.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(frontmatter.render(rm, rdoc.body))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, rp)
    rrel = str(rp.relative_to(root))
    if (root / ".git").exists():
        subprocess.run(["git", "-C", str(root), "add", "--", rrel],
                       capture_output=True, text=True)
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events",
                            identity={"machine": os.environ.get("HARNESS_MACHINE",
                                                                "local"),
                                      "session": "answer", "plane": "system",
                                      "req": req_id, "role": "lead"})
        w.append(RESUME_STATES[args.resume],
                 {"adjudication_ref": m["id"], "to": args.resume})
    except Exception:                                      # noqa: BLE001
        pass
    print(f"요청 재개: {req_id} → {args.resume}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
