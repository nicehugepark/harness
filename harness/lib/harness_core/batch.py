"""정본: A§5.3 착지 2단계와 배치 커밋 · A§5.4-⑤ 배치 잡 · A§7.3 인덱스 컴팩션
      · A§3.3-S3 중도 사망 복구 · A§9 백업 대상.

**쓰기 경로 3분리**(S§11-1)의 세 번째 갈래다: 원본 append 는 즉시, 파생은
append 즉시 + 컴팩션 배치, 커밋은 배치. 이벤트 1건마다 파생물을 전량 재생성하는
경로가 이 모듈에 없다 — 그것이 선행 운영의 세션 정지 사고를 만든 구조다.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from . import clock, policy


def _git(root, *args, check=False):
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r


def tracked(root, rel: str) -> bool:
    return _git(root, "ls-files", "--error-unmatch", "--", rel).returncode == 0


def batch_commit(root, message: str | None = None) -> dict:
    """A§5.3 ② 보존 확정 — 커밋은 배치다. 수동 커밋 경로를 정의하지 않는다."""
    root = pathlib.Path(root)
    staged = _git(root, "diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        return {"committed": False, "reason": "스테이징 없음", "files": []}
    files = staged.splitlines()
    msg = message or (f"기록 배치 커밋 — 문서 {len(files)}건 "
                      f"({clock.iso_local()})")
    r = _git(root, "-c", "user.email=harness@localhost",
             "-c", "user.name=harness", "commit", "-q", "-m", msg)
    return {"committed": r.returncode == 0, "files": files,
            "reason": r.stderr.strip()[:200] if r.returncode else ""}


def has_repo(root) -> bool:
    return (pathlib.Path(root) / ".git").exists()


def verify_commit_reality(root) -> dict:
    """"썼다"는 "보존됐다"가 아니다 — 커밋 실재를 **재조회**로 확인한다.

    반환값을 보존의 증거로 읽는 것이 실사고의 형태였다.
    """
    root = pathlib.Path(root)
    if not has_repo(root):
        # **저장소 부재는 '미보존'과 다른 사실이다.** 둘을 같은 칸에 세면
        # "문서가 유실되고 있다"와 "여기엔 저장소가 없다"가 구별되지 않고,
        # 후자는 설치 구성의 문제이지 배치 잡의 검출 대상이 아니다.
        return {"head": None, "missing": [], "no_repo": True}
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return {"head": None, "missing": ["HEAD 없음"]}
    listed = _git(root, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    have = set(listed)
    missing = []
    for p in sorted((root / "docs").rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if "/private/" in rel or rel.endswith(".tmp"):
            continue
        if rel not in have:
            missing.append(rel)
    return {"head": head.stdout.strip(), "missing": missing}


def find_untracked(root) -> list[str]:
    """A§3.3-S3 — public 트리의 미추적 파일은 **결함 상태**다."""
    root = pathlib.Path(root)
    r = _git(root, "ls-files", "--others", "--exclude-standard", "--", "docs")
    return [l for l in r.stdout.splitlines()
            if "/private/" not in l and not l.endswith(".tmp")]


def sweep_tmp(root) -> int:
    """중도 사망의 잔류물. 정본 트리에는 애초에 보이지 않는다(불가시)."""
    n = 0
    for p in pathlib.Path(root).rglob("*.tmp"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def compact_index(path) -> dict:
    """A§7.3 — id 당 최후 레코드만 남긴다. 상태 변경도 append 였으므로
    컴팩션이 곧 현재 상태다.

    파싱 실패 행은 건너뛰지 않고 **재생성 방아쇠**다 — 파생은 순수 재생성
    가능하므로 오염되면 재계산으로 복구한다. 조용히 건너뛰면 인덱스가
    거짓말을 하기 시작한다.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return {"regenerate": False, "records": 0}
    last: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return {"regenerate": True, "reason": "파싱 불가 행 — 전체 재생성 필요"}
        last[rec.get("id")] = rec
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in last.values()), encoding="utf-8")
    os.replace(tmp, path)
    return {"regenerate": False, "records": len(last)}


def mirror_incremental(root, backup_root) -> dict:
    """A§9 — vault/·config/ 는 문서 게이트를 지나지 않는 쓰기라 착지 동기화가
    불가하다. 변경분만 원자 복사한다. 상주 데몬 없이 배치로만 돈다."""
    root, backup_root = pathlib.Path(root), pathlib.Path(os.path.expanduser(backup_root))
    copied = 0
    for sub in ("vault", "config"):
        src = root / sub
        if not src.exists():
            continue
        for f in sorted(src.rglob("*")):
            if not f.is_file():
                continue
            dst = backup_root / "mirror" / sub / f.relative_to(src)
            if dst.exists() and dst.stat().st_mtime >= f.stat().st_mtime \
                    and dst.stat().st_size == f.stat().st_size:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, dst)
            copied += 1
    return {"copied": copied, "root": str(backup_root / "mirror")}


def drain_notify_queue(root, *, sender=None) -> dict:
    """훅이 예약한 발신 대기를 **배치가** 소비한다.

    훅은 네트워크를 하지 않으므로(R33) 발송 시점이 훅과 분리된다. 대신 대기
    레코드를 남겨 '끝났는데 아무도 모르는' 구간을 관측 가능하게 만든다 —
    탐지-무소비 루프를 만들지 않으려면 소비자가 코드에 있어야 한다.
    """
    from . import notify as _notify
    root = pathlib.Path(root)
    q = root / "config" / "local" / "notify-queue.jsonl"
    if not q.exists():
        return {"pending": 0, "sent": 0}
    lines = [l for l in q.read_text(encoding="utf-8").splitlines() if l.strip()]
    pending, out = [], []
    for l in lines:
        try:
            rec = json.loads(l)
        except json.JSONDecodeError:
            continue
        if rec.get("state") == "pending":
            pending.append(rec)
        out.append(rec)
    sent = 0
    pol = policy.load(root)
    for rec in pending:
        adj = {"id": f"notify-{rec['req']}", "question":
               f"요청 세션이 종료됐습니다: {rec['req']}",
               "options": [{"oid": "review", "label": "결과 확인"}],
               "path": f"docs/requests/…/{rec['req']}.md"}
        chans = {}
        cmd = pol.get("terminal_notify_command")
        if cmd:
            chans["terminal"] = lambda m, c=cmd: _notify.terminal_channel(m, c)
        if sender:
            chans["mobile"] = sender
        res = _notify.notify(adj, level=0, channels=chans,
                             append=lambda r: None)
        rec["state"] = "sent"
        rec["delivered"] = res["delivered"]
        sent += 1
    tmp = q.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out),
                   encoding="utf-8")
    os.replace(tmp, q)
    return {"pending": len(pending), "sent": sent}


def run_all(root) -> dict:
    """A§5.4-⑤ 배치 잡 전체. 멱등이며 어느 단계가 죽어도 다음 주기가 이어받는다."""
    root = pathlib.Path(root)
    pol = policy.load(root)
    out = {"at": clock.iso_local()}
    out["tmp_swept"] = sweep_tmp(root)
    untracked = find_untracked(root)
    if untracked:
        _git(root, "add", "--", *untracked)
        out["untracked_absorbed"] = untracked
    out["commit"] = batch_commit(root)
    out["reality"] = verify_commit_reality(root)
    idx = root / "derived/index/docs.jsonl"
    out["index"] = compact_index(idx)
    out["mirror"] = mirror_incremental(root, pol.get("backup_root",
                                                     "~/harness-backups"))
    out["notify"] = drain_notify_queue(root)
    return out
