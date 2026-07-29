#!/usr/bin/env python3
"""공개 대상 전문에 대한 정제 스캔 — 고정 스크립트.

정본: A§12(공유 패키징 새니타이즈) · PB§4(스캔은 대소문자를 구분한다) · PB-N5.

명령을 고정 스크립트로 두는 이유: 대소문자 무시 옵션이 붙는 순간 검증 기준
라벨 같은 대문자 약어가 소문자 판 번호 패턴으로 오탐돼 정제 판정이 통째로
무효가 된다(PB-N5 가 근원 처방으로 지목한 자리다).

스캔 범위는 **버전관리 추적 대상**이다 — 본문뿐 아니라 **파일 경로 자체**도
포함한다(경로가 내부 계보 토큰을 노출할 수 있다).
종료 코드: 0 = 검출 0건 · 1 = 검출 · 2 = 실행 오류.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_LIST = ROOT / "config/local/sanitize-scanlist.txt"

# 오탐 억제는 사유와 같은 넓이로만 한다(S§9.3-14). 각 항목에 사유를 붙인다.
SUPPRESS = [
    (r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+$",
     "도메인부에 점이 없다 — 이메일이 아니다. 목록의 이메일 패턴이 로컬파트@호스트 "
     "형태를 넓게 잡아 검증 기준 판 표기(X3) 같은 문자열을 삼킨다. 판별 근거는 "
     "TLD 구분자의 부재이며, 이 억제는 그 한 축만 닫는다", "match"),
    (r"harness-managed-asset/v\d+",
     "자산 관리 표식의 스키마 판이다. 선행 판 계보 라벨이 아니다 — 표식은 "
     "설치기가 관리물을 식별하는 고정 문자열이고, 그 안의 숫자는 표식 자신의 판이다",
     "context"),
    (r"criteria@v\d+",
     "검증 기준의 판 표기다(X3). 판 번호와 세대 라벨이 같은 글자를 쓰는 충돌은 "
     "PB-N5 가 기록한 축이며, 이 억제는 '기준 참조 뒤에 붙은 판 번호' 한 형태만 닫는다",
     "context"),
    (r"reset-full-allowed/v\d+",
     "설치기 허용 표식의 스키마 판이다. 관리 표식과 같은 클래스이며, 그 안의 "
     "숫자는 표식 자신의 판이지 선행 판 계보 라벨이 아니다", "context"),
    (r"lead-gate/\d+",
     "게이트 블록 스키마 판이다. 계보 라벨이 아니다", "context"),
    (r"(?<![A-Za-z0-9])v[12](?=\s*\||\s*동결|\s*내용|\s*변경|\s*신설|\s*작성|\s*로만)",
     "검증 기준의 판 번호다. 세대 라벨이 아니다 — 판 표기와 세대 라벨이 "
     "같은 글자를 쓰는 충돌은 PB-N5 가 기록한 축이다", "context"),
]


def tracked_files(root: pathlib.Path) -> list[pathlib.Path]:
    r = subprocess.run(["git", "-C", str(root), "ls-files"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return [root / p for p in r.stdout.splitlines()]
    # 저장소 이전 단계 — .gitignore 규칙을 모의한다
    def ignored(rel: str) -> bool:
        if rel.startswith(("vault/", "derived/", "config/", ".claude/", ".git/")):
            return True
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "docs" and parts[2] == "private":
            return True
        return "__pycache__" in parts or rel.endswith(".tmp")
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and not ignored(p.relative_to(root).as_posix())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--list", default=str(DEFAULT_LIST))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    lst = pathlib.Path(args.list)
    if not lst.exists():
        print(f"스캔 목록 부재: {lst} — 목록 없는 통과는 검출 0건이 아니라 "
              f"미검사다", file=sys.stderr)
        return 2
    pats = [l.strip() for l in lst.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    supp = [(re.compile(p), why, scope) for p, why, scope in SUPPRESS]

    files = tracked_files(root)
    hits, suppressed = [], []
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            body = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = ""
        for scope, text in (("path", rel), ("body", body)):
            for pat in pats:
                for m in re.finditer(pat, text):     # 대소문자 구분
                    frag = m.group(0)
                    ctx = text[max(0, m.start() - 30):m.start() + len(frag) + 20]
                    rec = {"file": rel, "scope": scope, "pattern": pat,
                           "match": frag[:60], "context": ctx.replace("\n", " ")[:110]}
                    why = next(
                        (w for rx, w, sc in supp
                         if rx.search(frag if sc == "match" else ctx)), None)
                    (suppressed if why else hits).append(
                        {**rec, **({"suppressed_because": why} if why else {})})

    if args.json:
        print(json.dumps({"files": len(files), "hits": hits,
                          "suppressed": suppressed}, ensure_ascii=False, indent=2))
    else:
        print(f"스캔 파일 {len(files)}개 · 패턴 {len(pats)}개 · "
              f"억제 규칙 {len(supp)}개 (대소문자 구분)")
        by = {}
        for h in hits:
            by.setdefault(h["pattern"], []).append(h)
        if not hits:
            print("검출 0건")
        for pat, occ in by.items():
            print(f"\n  {pat!r} — {len(occ)}건")
            for o in occ[:8]:
                print(f"     [{o['scope']}] {o['file']} :: {o['match']}")
            if len(occ) > 8:
                print(f"     … 외 {len(occ) - 8}건")
        if suppressed:
            print(f"\n억제 {len(suppressed)}건 (사유 동반 — 오탐):")
            for s in suppressed[:4]:
                print(f"     {s['file']} :: {s['match']}  ← {s['suppressed_because'][:60]}")
    return 1 if hits else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
