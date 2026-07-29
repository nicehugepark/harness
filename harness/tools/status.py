#!/usr/bin/env python3
"""가동 상태 한 장 — 폴링 주체가 읽을 최소 표면.

정본: B§4.5 결정 로그 · D§7.3 텔레메트리 · A§5.3 보존 확정 · S§7 침묵과 무위반의 구별.

**변화가 없으면 조용하다.** 매번 같은 것을 보고하면 읽는 쪽이 읽기를 그만두고,
그것이 경고 무소비의 형태다. `--since` 로 직전 스냅샷과의 diff 만 낸다.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import batch, clock, frontmatter, schema  # noqa: E402

SNAP_REL = "config/local/status-snapshot.json"


def collect(root: pathlib.Path) -> dict:
    reqs = []
    base = root / "docs" / "requests"
    if base.exists():
        for f in sorted(base.rglob("RQ-*.md")):
            try:
                m = frontmatter.parse(f.read_text(encoding="utf-8")).meta
            except Exception:                          # noqa: BLE001
                continue
            reqs.append({"id": m.get("id"), "state": schema.read_state(m),
                         "session_ref": m.get("session_ref"),
                         "title": m.get("title")})
    # 프롬프트 문구로 세션을 세면 문구를 바꿀 때마다 계수가 0이 된다.
    # 스폰 커맨드에 반드시 들어가는 **스크립트 경로**로 센다.
    pat = "workflows/request.js"
    live = subprocess.run(["pgrep", "-cf", pat], capture_output=True, text=True)
    sessions = int(live.stdout.strip() or 0)

    q = root / "config/local/notify-queue.jsonl"
    pending = 0
    if q.exists():
        for l in q.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(l).get("state") == "pending":
                    pending += 1
            except json.JSONDecodeError:
                pass

    timers = subprocess.run(
        ["systemctl", "--user", "list-timers", "--all", "--no-pager"],
        capture_output=True, text=True)
    tnames = [l.split()[-2:] for l in timers.stdout.splitlines() if "harness" in l]

    defects = []
    reality = batch.verify_commit_reality(root)
    if reality.get("missing"):
        defects.append(f"미보존 문서 {len(reality['missing'])}건")
    if reality.get("no_repo"):
        defects.append("저장소 부재 — 문서가 버전관리 밖")
    untracked = batch.find_untracked(root) if not reality.get("no_repo") else []
    if untracked:
        defects.append(f"미추적 {len(untracked)}건")
    if (root / "config/local/dispatch-kill").exists():
        defects.append("킬 스위치 ON — 신규 기동 정지")

    # 실패·사건 스트림의 신규분
    incidents = 0
    for f in (root / "docs" / "failures").rglob("FS-*.jsonl") \
            if (root / "docs" / "failures").exists() else []:
        try:
            incidents += sum(1 for l in f.read_text(encoding="utf-8").splitlines()
                             if '"incident.opened"' in l)
        except OSError:
            pass

    return {"at": clock.iso_local(),
            "requests": reqs,
            "by_state": {s: sum(1 for r in reqs if r["state"] == s)
                         for s in sorted({r["state"] for r in reqs if r["state"]})},
            "live_sessions": sessions,
            "notify_pending": pending,
            "timers": len(tnames),
            "incidents_opened": incidents,
            "defects": defects}


def digest(s: dict) -> str:
    import hashlib
    body = json.dumps({k: v for k, v in s.items() if k != "at"},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--since", action="store_true",
                    help="직전 스냅샷과 다를 때만 출력한다(변화 없으면 조용하다)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    cur = collect(root)
    snap = root / SNAP_REL

    changed = True
    if args.since and snap.exists():
        try:
            prev = json.loads(snap.read_text(encoding="utf-8"))
            changed = digest(prev) != digest(cur)
        except (OSError, json.JSONDecodeError):
            changed = True
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.since and not changed:
        print("NOCHANGE")
        return 0
    if args.json:
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0
    print(f"가동 상태 {cur['at']}")
    print(f"  요청 {len(cur['requests'])}건 · 상태별 {cur['by_state']}")
    for r in cur["requests"]:
        print(f"    {r['state']:10} {r['id']}  {str(r['title'])[:34]}")
    print(f"  실행 세션 {cur['live_sessions']} · 타이머 {cur['timers']} · "
          f"발신 대기 {cur['notify_pending']} · 신규 사건 {cur['incidents_opened']}")
    if cur["defects"]:
        for d in cur["defects"]:
            print(f"  ** 결함: {d}")
    else:
        print("  결함 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
