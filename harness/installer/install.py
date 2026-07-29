#!/usr/bin/env python3
"""설치기 — 백업 → 초기화 → 배치 → 검증 → 커밋 단일 트랜잭션.

정본: E§2.1 트랜잭션 상태기계 · E§2.3 백업 · E§2.4 초기화 2모드 · E§2.5 배치
      · E§2.6 청정 기동 검증 · E§2.7 커밋 · E§2.8 언인스톨 · E§2.9 롤백
      · E§2.10 저널 · E§3 멱등 · E§4 환경 인터뷰·자원 프로브 · E§5 플랫폼 프로파일

불변 셋.
1. 터미널 레코드(`committed`/`rolled_back`/`failed_dirty`) 없는 트랜잭션이
   발견되면 다른 어떤 일도 하기 전에 복구 분기부터 탄다.
2. 배치 목록은 어디에도 손으로 쓰지 않는다 — 글롭 열거 + 선언 파싱만으로 만든다.
3. 파일 조작은 `_touch()` 단일 함수를 경유하고, 그 함수가 범위 밖이면 실행을
   거부하고 저널에 `scope_violation` 을 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import uuid

ROOT_DEFAULT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DEFAULT / "harness" / "lib"))

from harness_core import clock, envelope, paths, policy, registry, roster  # noqa: E402

MANAGED_MARK = "harness-managed-asset/v1"
PHASES = ["planned", "interviewed", "backed_up", "reset_done", "installed",
          "verified", "committed"]
TERMINAL = {"committed", "rolled_back", "failed_dirty"}

# E§2.4.3 의 의사결정 상신에 대한 **의사결정권자 응답**(2026-07-29):
# 설치기는 초기화하지 않는다. 개인이 필요할 때 직접 한다.
# 근거 — 파괴적 조작의 기본값은 보존이고, 소거의 이득(간섭 제거)보다 손실
# (사용자 원본 소실 위험 · 신뢰 손상)이 크다는 판단이다. `reset-managed` 는
# 명시 선택으로 남고 `reset-full` 은 여전히 결정 문서 없이는 거부된다.
DEFAULT_RESET_MODE = "reset-none"

# E§2.4.2 reset-full — 설정 표면 전체 소거. 아래 목록은 **어느 모드에서도**
# 소거 대상이 아니다: 플랫폼 런타임 상태와 사용자 데이터이지 하네스 자산이 아니다.
# 목록을 좁게 유지하는 것이 요점이다 — 넓히면 "전체 소거"가 이름뿐이 되고,
# 좁히면 사용자 데이터가 날아간다. 판별 기준은 "하네스가 만들었는가"다.
RESET_FULL_PRESERVE = {
    "projects", "sessions", "history.jsonl", ".credentials.json", "plugins",
    "backups", "session-env", "shell-snapshots", "tasks", "downloads", "cache",
    "ide", "statsig", "todos", "file-history", "paste-cache", "remote",
    "mcp-needs-auth-cache.json", "settings.local.json", ".last-update-result.json",
}
# 결정 문서가 이 표식을 담고 있어야 reset-full 분기에 진입할 수 있다.
RESET_FULL_ALLOW_MARK = "reset-full-allowed/v1"
RESET_MODES = ["reset-none", "reset-managed", "reset-full", "incremental"]


# ══ 저널 (E§2.10) ═══════════════════════════════════════════════
class Journal:
    def __init__(self, root: pathlib.Path, txn_id: str, mode: str):
        self.root = root
        self.txn = txn_id
        self.mode = mode
        self.path = root / "config/install/journal" / f"{txn_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.phase = "planned"

    def write(self, *, phase=None, step="", action="write", target=None,
              outcome="ok", detail=None):
        rec = {"ts": clock.iso_local(), "txn_id": self.txn,
               "phase": phase or self.phase, "step": step, "action": action,
               "outcome": outcome}
        if phase == "planned" or action == "terminal":
            rec["mode"] = self.mode
        if target is not None:
            rec["target"] = str(target)
        if detail is not None:
            rec["detail"] = str(detail)[:400]     # 값 리터럴 금지 — 해시·참조만
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def enter(self, phase: str, step: str = ""):
        self.phase = phase
        self.write(phase=phase, step=step or phase, action="verify")


def unfinished_transactions(root: pathlib.Path) -> list[pathlib.Path]:
    d = root / "config/install/journal"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.jsonl")):
        terminal = False
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "terminal" and rec.get("phase") in TERMINAL:
                terminal = True
        if not terminal:
            out.append(p)
    return out


# ══ 플랫폼 프로파일 (E§5) ═══════════════════════════════════════
def detect_platform() -> str:
    s = sys.platform
    rel = platform.release().lower()
    if s == "darwin":
        return "macos"
    if s.startswith("win"):
        return "windows-native"
    if "microsoft" in rel:
        return "windows-wsl"
    return "linux"


def load_profile(root: pathlib.Path, plat: str) -> dict:
    p = root / "harness/installer/profiles" / f"{plat}.json"
    if not p.exists():
        raise SystemExit(f"플랫폼 프로파일 부재: {p} — 지원 플랫폼이 아니다")
    return json.loads(p.read_text(encoding="utf-8"))


# ══ 자원 프로브·산식 (E§4.4) ════════════════════════════════════
def probe_resources(profile: dict) -> dict:
    def run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True,
                                  text=True, timeout=30).stdout
        except Exception:                          # noqa: BLE001
            return ""
    out = {"probed_at": clock.iso_local(), "method": []}
    mem_total = mem_avail = cpu = disk = None
    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.exists():
        out["method"].append("cat /proc/meminfo")
        txt = meminfo.read_text()
        for line, key in (("MemTotal:", "mem_total_mb"),
                          ("MemAvailable:", "mem_available_mb")):
            for ln in txt.splitlines():
                if ln.startswith(line):
                    kb = int(ln.split()[1])
                    if key == "mem_total_mb":
                        mem_total = kb // 1024
                    else:
                        mem_avail = kb // 1024
    cpu = os.cpu_count()
    out["method"].append("os.cpu_count()")
    try:
        st = shutil.disk_usage(str(ROOT_DEFAULT))
        disk = st.free // (1024 * 1024)
        out["method"].append("shutil.disk_usage(실행 루트)")
    except OSError:
        pass
    out.update(mem_total_mb=mem_total, mem_available_mb=mem_avail,
               cpu_logical=cpu, disk_free_mb=disk)
    return out


def derive_limits(probe: dict, formulas: dict) -> dict:
    """E§4.4 산식. 파라미터는 formulas.json 에만 있다 — 코드 하드코딩 금지."""
    def val(key):
        # 파라미터는 {value, status, correction} 구조다 — 값 옆에 지위와 보정
        # 절차가 붙어 있어야 "잠정값을 상수로 단정"하는 경로가 생기지 않는다.
        v = formulas[key]
        return v["value"] if isinstance(v, dict) else v

    avail = probe.get("mem_available_mb") or 0
    cpu = probe.get("cpu_logical") or 1
    reserve = val("mem_reserve_floor_mb")
    budget = val("per_session_mem_budget_mb")
    by_mem = (avail - reserve) // budget
    by_cpu = int(cpu * val("cpu_factor"))
    cap = max(1, min(int(by_mem), by_cpu, val("hard_ceiling")))
    return {
        "concurrency_cap": cap,
        "mem_gate_floor_mb": reserve + budget,
        "per_session_mem_budget_mb": budget,
        "derived_at": clock.iso_local(),
        "formula_version": formulas["formula_version"],
        "inputs": {"mem_available_mb": avail, "cpu_logical": cpu},
        "status": "잠정 — 파라미터 초깃값이 미검증이다(E§9-M11 실측으로 보정)",
    }


def machine_identity(node: str, machine: str) -> str:
    """같은 호스트에서는 설치를 몇 번 해도 같은 ID 다 — ID 가 바뀌면 편성
    레코드가 매 설치마다 늘어나고 어느 것이 정본인지 판정 불가가 된다."""
    return hashlib.sha256(f"{node}|{machine}".encode()).hexdigest()[:12]


def merge_limits(derived: dict, existing: dict | None) -> dict:
    """B§4.4 — 사람 오버라이드가 판정식을 선점한다.

    산출값은 버리지 않고 `formula_derived_cap` 으로 **병기**한다. 덮는 것과
    숨기는 것은 다른 실패다 — 병기하면 괴리가 표면에 남아 보정 신호가 된다.
    """
    out = dict(derived)
    ov = ((existing or {}).get("limits") or {}).get("override")
    if ov and ov.get("value") is not None:
        out["formula_derived_cap"] = derived.get("concurrency_cap")
        out["concurrency_cap"] = ov["value"]
        out["override"] = ov
        out["status"] = ("사람 오버라이드 — 산식값과 병기해 괴리를 표면에 남긴다")
    return out


def find_cli(name: str, profile: dict, machine: dict | None = None) -> str | None:
    """플랫폼 CLI 탐색. 순서: 머신 레코드의 실측 확정값 → PATH → 프로파일 경로.

    비대화 셸의 PATH 에 없는 경우가 **정상적으로** 존재한다 — `ssh 호스트 '명령'`
    은 .bashrc·.profile 을 읽지 않으므로 ~/.local/bin 이 PATH 에서 사라진다
    (실측 2026-07-29: 비대화 PATH 에 없음, 로그인 셸 PATH 에는 있음).

    레코드값을 1순위로 두는 근거: 탐색은 추정이고 레코드는 측정이다. 다만
    레코드의 값이 실재하지 않으면 그것을 사실로 우기지 않고 탐색으로 내려간다.

    값은 머신 레코드(config/ — 버전관리 밖)에만 둔다. 코드나 저장소에 절대
    경로가 박히면 다른 사람의 설치가 깨지고 경로 자체가 노출이 된다(S§8).
    찾지 못하면 검증 축이 '검증 불가'가 되고 그것은 통과가 아니다.
    """
    recorded = ((machine or {}).get("cli") or {}).get(name)
    if recorded:
        cand = pathlib.Path(os.path.expanduser(recorded))
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    for d in (profile.get("bin_paths") or []):
        cand = pathlib.Path(os.path.expanduser(d)) / name
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


# ══ 배치 계획 파생 (E§2.5.2) ════════════════════════════════════
def parse_declaration(path: pathlib.Path) -> dict | None:
    if path.name == "hook.json":
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("declaration")
        except json.JSONDecodeError:
            return None
    if path.suffix == ".md":
        import yaml
        txt = path.read_text(encoding="utf-8")
        if not txt.startswith("---"):
            return None
        try:
            head = txt.split("---", 2)[1]
            return (yaml.safe_load(head) or {}).get("declaration")
        except (yaml.YAMLError, IndexError):
            return None
    if path.suffix == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("declaration")
        except json.JSONDecodeError:
            return None
    return None


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sibling_payload(f: pathlib.Path, decl: dict) -> list[pathlib.Path]:
    """선언 파일과 **같은 단위로 배치되어야 하는 동반 파일**.

    훅 자산의 단위는 파일이 아니라 디렉토리다 — 선언은 `hook.json` 에 있고
    실행체는 그 옆에 있다(E§2.5.1: "스크립트 실행체는 동반 hook.json").
    선언을 가진 파일만 배치하면 **등록은 되고 실행체는 없는 상태**가 만들어지고,
    그 상태에서 훅은 fail-open 이 아니라 fail-closed 로 전 도구 호출을 막는다
    (실측 2026-07-29T09:52 트랜잭션 d55103625edc — 이 함수가 그 회귀 방지다).
    """
    if decl.get("asset_kind") != "hook" or f.name != "hook.json":
        return []
    return [s for s in sorted(f.parent.iterdir())
            if s.is_file() and s.name != "hook.json"]


def derive_plan(root: pathlib.Path, profile: dict, plat: str) -> list[dict]:
    assets = root / "harness/assets"
    plan: list[dict] = []
    for f in sorted(assets.rglob("*")):
        if not f.is_file():
            continue
        decl = parse_declaration(f)
        if not decl:
            continue
        plats = decl.get("platforms", [])
        if plat not in plats and "all" not in plats:
            continue
        if decl.get("managed_mark") != MANAGED_MARK:
            continue
        kind = decl["asset_kind"]
        dest = dest_for(root, profile, kind, decl, f)
        plan.append({"asset_id": decl["asset_id"], "kind": kind, "src": f,
                     "dest": dest, "src_hash": sha256_file(f), "decl": decl})
        for extra in sibling_payload(f, decl):
            plan.append({"asset_id": f"{decl['asset_id']}#{extra.name}",
                         "kind": kind, "src": extra,
                         "dest": dest.parent / extra.name,
                         "src_hash": sha256_file(extra), "decl": decl})
    # 참조 그래프 검증 — 미해결 참조 1건이라도 있으면 배치 시작 전 실패
    have = {p["asset_id"] for p in plan}
    unresolved = []
    for p in plan:
        for req in (p["decl"].get("requires") or []) + \
                   (p["decl"].get("skills_required") or []):
            if req not in have:
                unresolved.append((p["asset_id"], req))
    if unresolved:
        raise SystemExit(f"참조 그래프 미해결 {len(unresolved)}건: {unresolved[:5]}")
    order = {"scaffold": 0, "skill": 1, "agent": 2, "hook": 3,
             "settings-fragment": 4}
    plan.sort(key=lambda p: order.get(p["kind"], 9))
    return plan


def dest_for(root, profile, kind, decl, src: pathlib.Path) -> pathlib.Path:
    d = profile["dest"]
    if kind == "agent":
        return root / d["agents"] / src.name
    if kind == "skill":
        return root / d["skills"] / src.parent.name / src.name
    if kind == "hook":
        return root / d["hooks"] / src.parent.name / src.name
    if kind == "settings-fragment":
        return root / d["settings_fragments"] / src.name
    if kind == "scaffold":
        return root / decl["target_root"] / src.name
    raise ValueError(f"알 수 없는 asset_kind: {kind}")


# ══ 범위 게이트 ═════════════════════════════════════════════════
class Scope:
    """E§2.4.4 — 범위 밖은 존재 자체를 모르는 것처럼 동작한다."""

    def __init__(self, root: pathlib.Path, profile: dict, journal: Journal):
        self.root = root.resolve()
        self.backup_root = pathlib.Path(
            os.path.expanduser(profile["backup_root"])).resolve()
        self.journal = journal
        self.allow = [
            self.root / ".claude",
            pathlib.Path(os.path.expanduser("~/.claude")).resolve(),
            self.root / "config",
            self.backup_root,
        ]
        self.forbid = [
            self.root / "docs", self.root / "vault", self.root / ".git",
            self.root / "config/names", self.root / "config/machines",
        ]

    def check(self, target: pathlib.Path, *, write: bool) -> bool:
        t = pathlib.Path(target).resolve()
        for f in self.forbid:
            if t == f or f in t.parents:
                self.journal.write(action="scope_violation", target=t,
                                   outcome="fail", detail="불가침 목록")
                return False
        if any(a == t or a in t.parents for a in self.allow):
            return True
        self.journal.write(action="scope_violation", target=t, outcome="fail",
                           detail="화이트리스트 밖")
        return False


def _touch(scope: Scope, journal: Journal, action: str, target: pathlib.Path,
           fn) -> bool:
    """설치기의 모든 파일 조작이 경유하는 단일 함수."""
    if not scope.check(target, write=True):
        return False
    try:
        fn()
    except OSError as exc:
        journal.write(action=action, target=target, outcome="fail", detail=str(exc))
        return False
    journal.write(action=action, target=target, outcome="ok")
    return True


# ══ 백업 (E§2.3) ════════════════════════════════════════════════
BACKUP_COVERAGE = ["user_claude_config", "project_claude_config", "docs_private",
                   "vault", "local_config"]


def coverage_sources(root: pathlib.Path,
                     user_home: pathlib.Path | None = None) -> dict[str, list[pathlib.Path]]:
    """E§2.3 백업 범위. 사용자 홈은 프로파일 값이다 — 리허설·시험이 실사용자
    홈을 뜨지 않게 하려면 이 축이 외재화돼 있어야 한다(S§8 엔진 청결)."""
    home = pathlib.Path(user_home or os.path.expanduser("~"))
    home_claude = home / ".claude"
    return {
        "user_claude_config": [home_claude] if home_claude.exists() else [],
        "project_claude_config": [root / ".claude"] if (root / ".claude").exists() else [],
        "docs_private": sorted((root / "docs").glob("*/private")),
        "vault": [root / "vault"] if (root / "vault").exists() else [],
        "local_config": [root / "config"] if (root / "config").exists() else [],
    }


SKIP_DIRS = {"projects", "sessions", "shell-snapshots", "file-history",
             "paste-cache", "cache", "backups", "remote", "statsig",
             "__pycache__", "node_modules", ".git"}


def snapshot(root: pathlib.Path, profile: dict, journal: Journal, txn: str,
             trigger: str) -> pathlib.Path:
    backup_root = pathlib.Path(os.path.expanduser(profile["backup_root"]))
    if backup_root.resolve() == root.resolve() or root.resolve() in backup_root.resolve().parents:
        raise SystemExit("백업 루트가 실행 루트 하위다 — 자기 파괴 경로(E§1.1)")
    snap_id = f"{clock.now_utc():%Y%m%dT%H%M%SZ}-{txn}"
    partial = backup_root / "snapshots" / f"{snap_id}.partial"
    partial.mkdir(parents=True, exist_ok=True)

    entries, excluded, total = [], [], 0
    live_journal = journal.path.resolve()
    for cov, srcs in coverage_sources(root, profile.get("user_home")).items():
        for src in srcs:
            for f in sorted(src.rglob("*")) if src.is_dir() else [src]:
                if not f.is_file():
                    continue
                if set(f.parts) & SKIP_DIRS:
                    excluded.append({"path": str(f), "reason": "휘발성·재생성 가능"})
                    continue
                if f.resolve() == live_journal:
                    # 자기 트랜잭션의 저널은 백업 대상이 아니다 — 백업의 기록을
                    # 백업이 뜨는 자기참조이고, 복원해도 의미가 없다.
                    excluded.append({"path": str(f),
                                     "reason": "현 트랜잭션의 진행 중 저널"})
                    continue
                # 보관 경로는 **커버리지 소스 기준 상대 경로**로 만든다.
                # 파일 이름만 쓰는 폴백은 같은 basename 을 가진 형제 파일을
                # 한 자리에 겹쳐 조용히 유실시킨다(실사고: 27파일 → 3경로, 24건 유실).
                try:
                    rel = pathlib.Path(src.name) / f.relative_to(src)
                except ValueError:
                    rel = pathlib.Path(src.name) / f.name
                dst = partial / cov / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                st = f.stat()
                entries.append({
                    "source_path": str(f),
                    "archived_path": (pathlib.Path(cov) / rel).as_posix(),
                    "kind": "file",
                    # 해시의 대상은 **보관본**이다. 소스 기준으로 재면 복사 도중
                    # 변한 파일에서 검증이 어긋나고, 그것이 실사고였다
                    # ("백업 검증 실패 — 460/484"). 복원이 쓰는 것도 보관본이므로
                    # 보관본 무결성이 검증해야 할 사실이다.
                    "sha256": sha256_file(dst),
                    "size_bytes": st.st_size,
                    "mtime": clock.iso_local(
                        __import__("datetime").datetime.fromtimestamp(st.st_mtime)),
                })
                total += st.st_size

    verified = sum(1 for e in entries
                   if (partial / e["archived_path"]).exists()
                   and sha256_file(partial / e["archived_path"]) == e["sha256"])
    manifest = {
        "snapshot_id": snap_id, "created_at": clock.iso_local(),
        "machine_id": platform.node(), "trigger": trigger,
        "installer_version": "0.1.0", "coverage": BACKUP_COVERAGE,
        "entries": entries, "excluded": excluded,
        "verify": {"entries_total": len(entries), "hash_verified": verified,
                   "verified_at": clock.iso_local()},
        "size_total_bytes": total,
        "largest": sorted(entries, key=lambda e: -e["size_bytes"])[:5],
    }
    (partial / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    final = partial.with_suffix("")
    os.replace(partial, final)
    journal.write(phase="backed_up", step="snapshot", action="hash",
                  target=final, outcome="ok",
                  detail=f"{verified}/{len(entries)} 해시 검증")
    if verified != len(entries):
        raise SystemExit(f"백업 검증 실패 — {verified}/{len(entries)}")
    return final


# 제거 순서 — 등록 표면이 실행체보다 먼저다.
# 순서를 뒤집으면 "등록은 남고 실행체는 없는" 창이 생기고, 그 창에서 훅이
# fail-closed 로 전 도구 호출을 막는다(실측된 폭발 지점).
REMOVAL_ORDER = ["settings-fragment", "hook", "agent", "skill", "scaffold"]


def transaction_additions(journal_path: pathlib.Path,
                          snapshot_dir: pathlib.Path) -> list[pathlib.Path]:
    """트랜잭션이 **새로 만든** 경로. 저널의 copy·write 대상 중 스냅샷 매니페스트에
    없던 것이다.

    E§2.9 는 롤백 수단을 "스냅샷 전체 복원"으로 두고 "스냅샷 복원은 멱등이다"를
    근거로 삼았다. 그 주장은 성립하지 않는다 — 복사는 **있던 것을 되돌릴** 뿐
    **새로 생긴 것을 지우지** 못한다. 실사고에서 백업 시점에 없던 설정 표면이
    살아남아 지워진 실행체를 가리켰다. 이 함수가 그 갭을 메운다.

    삭제 대상을 저널에서 얻는 것이 핵심이다: "하네스 관리물처럼 보이는 것"을
    지우면 불가침 경계를 넘고, 트랜잭션이 실제로 만든 것만 지우면 넘지 않는다.
    """
    man = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    pre_existing = {e["source_path"] for e in man["entries"]}
    added: list[pathlib.Path] = []
    for ln in journal_path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if rec.get("action") not in ("copy", "write"):
            continue
        if rec.get("outcome") != "ok":
            continue
        t = rec.get("target")
        if t and t not in pre_existing:
            p = pathlib.Path(t)
            if p not in added:
                added.append(p)
    return added


def _removal_rank(p: pathlib.Path) -> int:
    s = str(p)
    if s.endswith("settings.json") or "fragments" in s:
        return REMOVAL_ORDER.index("settings-fragment")
    for kind, token in (("hook", "/hooks/"), ("agent", "/agents/"),
                        ("skill", "/skills/")):
        if token in s:
            return REMOVAL_ORDER.index(kind)
    return REMOVAL_ORDER.index("scaffold")


def restore(snapshot_dir: pathlib.Path, journal: Journal, *,
            root: pathlib.Path | None = None, scope=None) -> bool:
    """롤백 = ①트랜잭션 추가분 제거(등록 표면 먼저) + ②스냅샷 복원.

    두 단계가 한 단위다. ① 없이 ② 만 하면 설치 전 상태가 되지 않는다.
    """
    removed = 0
    if root is not None:
        try:
            added = transaction_additions(journal.path, snapshot_dir)
        except (OSError, json.JSONDecodeError):
            added = []
        for p in sorted(added, key=_removal_rank):
            if scope is not None and not scope.check(p, write=True):
                continue
            try:
                if p.is_file():
                    p.unlink()
                    removed += 1
                    journal.write(phase="rolling_back", step="remove-additions",
                                  action="delete", target=p, outcome="ok")
            except OSError as exc:
                journal.write(phase="rolling_back", step="remove-additions",
                              action="delete", target=p, outcome="fail",
                              detail=str(exc))

    man = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    ok = 0
    for e in man["entries"]:
        src = snapshot_dir / e["archived_path"]
        dst = pathlib.Path(e["source_path"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if sha256_file(dst) == e["sha256"]:
                ok += 1
        except OSError:
            pass
    journal.write(phase="rolling_back", step="restore", action="copy",
                  outcome="ok" if ok == len(man["entries"]) else "fail",
                  detail=f"복원 {ok}/{len(man['entries'])} · 추가분 제거 {removed}")
    return ok == len(man["entries"])


# ══ 초기화 (E§2.4) ══════════════════════════════════════════════
def is_harness_managed(path: pathlib.Path, manifest: dict | None) -> bool:
    if manifest:
        if str(path) in {a["dest_path"] for a in manifest.get("assets", [])}:
            return True
    try:
        return MANAGED_MARK in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def reset_full_plan(surface: pathlib.Path) -> dict:
    """소거·보존 대상을 **삭제 전에** 전건 열거한다.

    E§2.4.2 는 "삭제 예정 목록을 전건 열거해 제시하고, 사용자가 그 목록을 본
    상태에서 승인해야 진행한다"를 요구한다. 열거와 삭제를 같은 함수에 두면
    그 순서가 코드로 보장되지 않으므로 분리한다 — 이 함수는 아무것도 지우지 않는다.
    """
    delete, preserve = [], []
    if not surface.exists():
        return {"delete": [], "preserve": [], "settings": None}
    for child in sorted(surface.iterdir()):
        if child.name in RESET_FULL_PRESERVE:
            preserve.append(child)
            continue
        if child.name == "settings.json":
            preserve.append(child)          # 파일은 남기고 훅 등록만 걷는다
            continue
        if child.is_dir():
            delete += [f for f in sorted(child.rglob("*")) if f.is_file()]
            delete.append(child)
        else:
            delete.append(child)
    return {"delete": delete, "preserve": preserve,
            "settings": surface / "settings.json"}


def reset_full_allowed(root: pathlib.Path, *, acknowledged: bool) -> tuple[bool, str]:
    """3전제 중 둘을 검사한다(백업 완결은 상태기계가 별도로 강제).

    ①결정 문서의 허용 레코드 ②삭제 예정 목록 열람 승인.
    둘 다 없으면 분기 진입 자체가 불가능하다 — 코드 경로가 하나뿐이다.
    """
    found = None
    base = root / "docs" / "decisions"
    if base.exists():
        for f in base.rglob("DN-*.md"):
            try:
                if RESET_FULL_ALLOW_MARK in f.read_text(encoding="utf-8"):
                    found = f
                    break
            except OSError:
                continue
    if not found:
        return False, (f"결정 문서에 허용 레코드({RESET_FULL_ALLOW_MARK})가 없다 — "
                       f"reset-full 은 결정 착지 전에는 실행 불가다")
    if not acknowledged:
        return False, "삭제 예정 목록 열람 승인이 없다"
    return True, str(found.relative_to(root))


def reset_full_apply(surface: pathlib.Path, journal: "Journal") -> int:
    """소거 실행. 등록 표면(훅)을 먼저 걷고 실행체를 나중에 지운다."""
    plan = reset_full_plan(surface)
    removed = 0
    settings = plan["settings"]
    if settings and settings.exists():
        try:
            cfg = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        if "hooks" in cfg:
            cfg.pop("hooks")
            tmp = settings.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, settings)
            journal.write(phase="reset_done", step="strip-hooks", action="write",
                          target=settings, outcome="ok")
    for p in plan["delete"]:
        try:
            if p.is_file():
                p.unlink()
                removed += 1
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        except OSError as exc:
            journal.write(phase="reset_done", action="delete", target=p,
                          outcome="fail", detail=str(exc))
    journal.write(phase="reset_done", step="reset-full", action="delete",
                  outcome="ok",
                  detail=f"{removed}건 소거 · 보존 {len(plan['preserve'])}항목")
    return removed


def find_orphans(root, profile, plan) -> list:
    """배치 표면에 있으나 현 배치 계획에 없는 **하네스 관리물**.

    초기화를 하지 않으면 로스터에서 빠진 자산이 남는다. 지우지는 않되
    보이게 한다 — 침묵하면 배치본과 표가 갈린 사실을 아무도 모르고,
    그것이 "나열을 감시하는 장치가 또 필요해지는" 경로다(S§10-7).
    """
    planned = {str(p["dest"]) for p in plan}
    orphans = []
    for base_rel in (profile["dest"]["agents"], profile["dest"]["skills"],
                     profile["dest"]["hooks"]):
        base = root / base_rel
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or str(f) in planned:
                continue
            try:
                if MANAGED_MARK in f.read_text(encoding="utf-8", errors="ignore"):
                    orphans.append(f)
            except OSError:
                pass
    return orphans


def reset(root, profile, journal, scope, mode, manifest) -> int:
    if mode in ("incremental", "reset-none"):
        journal.write(phase="reset_done", step=mode, action="skipped",
                      outcome="ok", detail="초기화하지 않는다 — 의사결정권자 확정")
        return 0
    if mode == "reset-full":
        ok, why = reset_full_allowed(root, acknowledged=bool(
            getattr(reset, "_acknowledged", False)))
        if not ok:
            raise SystemExit(f"reset-full 거부 — {why} (E§2.4.2)")
        surface = pathlib.Path(os.path.expanduser(
            profile.get("user_home") or "~")) / ".claude"
        n = reset_full_apply(surface, journal)
        n += reset_full_apply(root / ".claude", journal)
        return n
    removed = 0
    for base_rel in (profile["dest"]["agents"], profile["dest"]["skills"],
                     profile["dest"]["hooks"]):
        base = root / base_rel
        if not base.exists():
            continue
        for f in sorted(base.rglob("*"), reverse=True):
            if f.is_file() and is_harness_managed(f, manifest):
                if _touch(scope, journal, "delete", f, f.unlink):
                    removed += 1
    journal.write(phase="reset_done", step="reset-managed", action="delete",
                  outcome="ok", detail=f"{removed}건 소거 · foreign 불가촉")
    return removed


# ══ 배치 (E§2.5) ════════════════════════════════════════════════
def apply_plan(root, plan, journal, scope, incremental: bool,
               manifest: dict | None = None) -> tuple[list, list]:
    """E§2.4.1 클래스 판정식은 **표식 또는 매니페스트 조인**이다.

    둘 중 하나만 쓰면 판정식이 절반만 구현된다 — 표식 도입 이전에 배치된 파일은
    표식이 없어서 설치기가 자기가 놓은 것을 '사용자 커스텀'으로 보류하고,
    수정본이 영원히 배치되지 않는다(실측 2026-07-29T11:29 — 보류 5건 반복).
    """
    ours = {a.get("dest_path") for a in (manifest or {}).get("assets", [])}
    placed, conflicts = [], []
    for item in plan:
        dest: pathlib.Path = item["dest"]
        if dest.exists() and incremental:
            existing = dest.read_text(encoding="utf-8", errors="ignore")
            if MANAGED_MARK not in existing and str(dest) not in ours:
                conflicts.append({"asset_id": item["asset_id"],
                                  "dest": str(dest), "reason": "사용자 커스텀 보존"})
                journal.write(action="write", target=dest, outcome="skipped",
                              detail="record_conflict")
                continue
            if sha256_file(dest) == item["src_hash"]:
                journal.write(action="write", target=dest, outcome="skipped",
                              detail="동일 해시 — 멱등 생략")
                placed.append(item)
                continue

        def do(src=item["src"], dst=dest):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if _touch(scope, journal, "copy", dest, do):
            placed.append(item)
    return placed, conflicts


def apply_settings(root, profile, plan, journal, scope, limits) -> list[dict]:
    """설정 단편 적용 — 훅 등록도 여기서 한다(설정은 코드와 한 몸 배포)."""
    surfaces: dict[pathlib.Path, dict] = {}
    keys_written = []
    for item in plan:
        if item["kind"] != "settings-fragment":
            continue
        decl = item["decl"]
        target = root / profile["surfaces"][decl["target_surface"]]
        cur = surfaces.get(target)
        if cur is None:
            cur = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            surfaces[target] = cur
        payload = json.loads(item["src"].read_text(encoding="utf-8"))
        for key, value in (payload.get("declaration", {}).get("keys") or {}).items():
            value = resolve_ref(value, limits)
            strategy = decl.get("merge_strategy", "set-if-absent")
            if strategy == "set-if-absent" and key in cur:
                continue
            if strategy == "append-array":
                base = cur.get(key) or []
                for v in value:
                    if v not in base:
                        base.append(v)
                cur[key] = base
            else:
                cur[key] = value
            keys_written.append({"surface": decl["target_surface"], "key": key,
                                 "managed": True})
    # 훅 등록 — 배치된 훅 자산의 선언에서 파생한다(수기 나열 금지)
    hooks_cfg: dict = {}
    for item in plan:
        if item["kind"] != "hook":
            continue
        if item["src"].name != "hook.json":
            continue
        decl = item["decl"]
        runner = item["dest"].parent / "run.py"
        entry = {"type": "command",
                 "command": f"HARNESS_ROOT={root} HARNESS_LIB={root}/harness/lib "
                            f"python3 {runner}",
                 "timeout": decl.get("timeout_s", 15)}
        group = {"hooks": [entry]}
        if decl.get("matcher"):
            group["matcher"] = decl["matcher"]
        hooks_cfg.setdefault(decl["hook_event"], []).append(group)
    target = root / profile["surfaces"]["project-settings"]
    cur = surfaces.setdefault(
        target, json.loads(target.read_text(encoding="utf-8")) if target.exists() else {})
    cur["hooks"] = hooks_cfg
    keys_written.append({"surface": "project-settings", "key": "hooks",
                         "managed": True})

    for path, data in surfaces.items():
        def do(p=path, d=data):
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, p)
        _touch(scope, journal, "write", path, do)
    return keys_written


def resolve_ref(value, limits):
    if isinstance(value, dict) and "from" in value:
        src, _, field = str(value["from"]).partition(":")
        if src == "machine-config":
            cur = {"limits": limits}
            for part in field.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            return cur
    return value


# ══ 검증 (E§2.6) ════════════════════════════════════════════════
def verify(root, profile, plan, journal, clean_home: bool,
           machine: dict | None = None) -> dict:
    res = {"axes": {}, "unverifiable": []}

    # A2 — 로스터 기대 집합 ⊆ 등록된 에이전트 집합
    expected = {p["decl"]["role_key"] for p in plan if p["kind"] == "agent"}
    present = {f.stem for f in (root / profile["dest"]["agents"]).glob("*.md")} \
        if (root / profile["dest"]["agents"]).exists() else set()
    res["axes"]["A2_roster"] = expected <= present
    res["axes"]["A2_detail"] = f"{len(expected & present)}/{len(expected)}"

    # A3 — 훅 기대 집합 ⊆ 설정 표면 등록 집합
    surface = root / profile["surfaces"]["project-settings"]
    cfg = json.loads(surface.read_text(encoding="utf-8")) if surface.exists() else {}
    registered = set(cfg.get("hooks", {}))
    want = {p["decl"]["hook_event"] for p in plan
            if p["kind"] == "hook" and p["src"].name == "hook.json"}
    res["axes"]["A3_hooks"] = want <= registered
    res["axes"]["A3_detail"] = f"{sorted(want)} ⊆ {sorted(registered)}"

    # A5 — 설정 단편 keys 전건이 유효값으로 관측됨(기입 ≠ 적용이므로 재독)
    res["axes"]["A5_settings"] = bool(cfg)

    # A8(신설) — 등록된 훅 명령의 실행체가 전부 실재한다.
    # 근거: 실행체 부재는 fail-open 이 아니라 fail-closed 다 — 등록만 남으면
    # 전 도구 호출이 막힌다(실측 2026-07-29 트랜잭션 d55103625edc).
    dangling = []
    for _ev, groups in (cfg.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                script = str(h.get("command", "")).split()[-1]
                if script.endswith(".py") and not pathlib.Path(script).exists():
                    dangling.append(script)
    res["axes"]["A8_hook_exec"] = not dangling
    res["axes"]["A8_detail"] = f"매달린 등록 {len(dangling)}건"

    # A6 — 저널에 scope_violation 0건
    viol = sum(1 for ln in journal.path.read_text(encoding="utf-8").splitlines()
               if '"scope_violation"' in ln)
    res["axes"]["A6_scope"] = viol == 0
    res["axes"]["A6_detail"] = f"{viol}건"

    # A1·A4 — 헤드리스 프로브 세션 + SessionStart 센티널 이벤트 착지
    claude = find_cli("claude", profile, machine=machine)
    if not claude:
        res["unverifiable"].append("A1/A4 — claude 실행체 부재")
        res["axes"]["A1_headless"] = None
        res["axes"]["A4_sentinel"] = None
    else:
        before = set((root / "docs/audit").rglob("*.jsonl"))
        proc = subprocess.run(
            [claude, "-p", "Reply with the single word: ok",
             "--output-format", "json", "--permission-mode", "auto",
             "--tools", "Read"],
            capture_output=True, text=True, cwd=str(root), timeout=300,
            env={**os.environ, "HARNESS_ROOT": str(root),
                 "HARNESS_LIB": str(root / "harness/lib"),
                 # 프로브는 에이전트 응답이 아니다 — system 평면
                 "HARNESS_PLANE": "system"})
        res["axes"]["A1_headless"] = proc.returncode == 0
        after = set((root / "docs/audit").rglob("*.jsonl"))
        started = False
        for f in after:
            try:
                for ln in f.read_text(encoding="utf-8").splitlines():
                    if '"session.start"' in ln:
                        started = True
            except OSError:
                pass
        res["axes"]["A4_sentinel"] = started
        res["axes"]["A4_detail"] = f"신규 스트림 파일 {len(after - before)}개"

    passed = [k for k, v in res["axes"].items()
              if not k.endswith("_detail") and v is True]
    failed = [k for k, v in res["axes"].items()
              if not k.endswith("_detail") and v is False]
    res["pass"] = not failed
    res["passed"], res["failed"] = passed, failed
    journal.write(phase="verified", step="verify_pass", action="verify",
                  outcome="ok" if res["pass"] else "fail",
                  detail=f"통과 {passed} / 불통과 {failed} / "
                         f"검증 불가 {res['unverifiable']}")
    return res


# ══ 메인 트랜잭션 ═══════════════════════════════════════════════
def cmd_install(args) -> int:
    root = pathlib.Path(args.root).resolve()
    plat = args.platform or detect_platform()
    profile = load_profile(root, plat)
    txn = uuid.uuid4().hex[:12]

    stale = unfinished_transactions(root)
    if stale and not args.force_new:
        print(f"미완 트랜잭션 {len(stale)}건 발견 — 복구 분기가 먼저다:")
        for s in stale:
            print(f"  {s}")
        print("복원 권고: install.py rollback --snapshot <경로>")
        return 1

    mode = args.mode
    j = Journal(root, txn, mode)
    j.write(phase="planned", step="open", action="write", outcome="ok",
            detail=f"platform={plat} mode={mode}")

    manifest_path = root / "config/install/installed-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) \
        if manifest_path.exists() else None
    if manifest and mode == "reset-none":
        mode = "incremental"
        j.mode = mode

    scope = Scope(root, profile, j)
    reset._acknowledged = bool(getattr(args, "ack_deletion_list", False))
    if mode == "reset-full":
        surface = pathlib.Path(os.path.expanduser(
            profile.get("user_home") or "~")) / ".claude"
        plan_rf = reset_full_plan(surface)
        print(f"reset-full 삭제 예정 {len(plan_rf['delete'])}건 · "
              f"보존 {len(plan_rf['preserve'])}항목")
        for x in plan_rf["delete"][:12]:
            print(f"    삭제: {x}")
        if len(plan_rf["delete"]) > 12:
            print(f"    … 외 {len(plan_rf['delete']) - 12}건")
        for x in plan_rf["preserve"]:
            print(f"    보존: {x.name}")
        j.write(phase="planned", step="reset-full-list", action="verify",
                outcome="ok",
                detail=f"삭제 {len(plan_rf['delete'])} · 보존 {len(plan_rf['preserve'])}"
                       f" · 열람승인={reset._acknowledged}")

    # ── 인터뷰 (E§4) ────────────────────────────────────────────
    j.enter("interviewed", "interview")
    probe = probe_resources(profile)
    formulas = json.loads(
        (root / "harness/installer/formulas.json").read_text(encoding="utf-8"))
    limits = derive_limits(probe, formulas)
    machine_id = machine_identity(platform.node(), platform.machine())
    mrec = {
        "machine_id": machine_id, "display_alias": args.alias or platform.node(),
        "platform": plat, "role": "primary", "transport": "local",
        "work_root": str(root), "probe": probe, "limits": limits,
        "cli": {},
        "status": "active", "pending": [], "created": clock.iso_local(),
        "updated": clock.iso_local(),
    }
    mpath = root / "config/machines" / f"{machine_id}.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    if prev is None:
        # 같은 별칭의 손 작성 레코드가 있으면 그것을 이 머신의 정본으로 흡수한다 —
        # 같은 물리 머신에 레코드가 두 벌이면 어느 쪽이 정본인지 판정 불가다.
        for other in (root / "config/machines").glob("*.json"):
            try:
                cand = json.loads(other.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if cand.get("display_alias") == (args.alias or platform.node()) \
                    and other.name != mpath.name:
                prev = cand
                other.unlink()
                j.write(action="delete", target=other, outcome="ok",
                        detail="같은 별칭의 중복 편성 레코드를 흡수")
                break
    limits = merge_limits(limits, prev)
    # CLI 경로를 **실측해 레코드에 남긴다**. 다음 설치는 탐색이 아니라 이 값을
    # 먼저 본다 — 같은 것을 매번 다시 추정하지 않기 위해서다.
    mrec["cli"] = dict((prev or {}).get("cli") or {})
    found_cli = find_cli("claude", profile, machine=prev)
    if found_cli:
        mrec["cli"]["claude"] = found_cli
        mrec["cli"]["_measured_at"] = clock.iso_local()
    # 병합 결과를 레코드에 **다시 넣는다**. 이름만 재할당하면 mrec 은 병합 전
    # 값을 계속 가리키고, 출력과 파일이 갈린다 — 검증 축 A5 가 "기입 ≠ 적용"이라
    # 적은 실패를 설치기 자신이 내게 된다(실사고 2026-07-29T03:39: 출력 50, 파일 8).
    mrec["limits"] = limits
    mpath.write_text(json.dumps(mrec, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    written = json.loads(mpath.read_text(encoding="utf-8"))     # 재독 대조
    if written["limits"]["concurrency_cap"] != limits["concurrency_cap"]:
        raise SystemExit("머신 레코드 기입값과 재독값이 다르다")
    j.write(action="probe", target=mpath, outcome="ok",
            detail=f"concurrency_cap={limits['concurrency_cap']}")

    # 이름 레지스트리 — 없는 역할만 채운다(재질문 금지)
    missing = registry.missing_roles(root)
    if missing:
        if args.names:
            names = json.loads(pathlib.Path(args.names).read_text(encoding="utf-8"))
        else:
            names = {}
        for rk in missing:
            registry.register(root, rk, names.get(rk, rk), actor="installer")
        j.write(action="ask", outcome="ok",
                detail=f"이름 등록 {len(missing)}건 (미응답분은 역할 키를 잠정 "
                       f"표시명으로 — 변경 인터뷰로 교체 가능)")
    policy.write_defaults(root)

    # ── 백업 ────────────────────────────────────────────────────
    j.enter("backed_up", "snapshot")
    try:
        snap = snapshot(root, profile, j, txn, trigger="install")
    except SystemExit as exc:
        j.write(phase="failed_dirty", action="terminal", outcome="fail",
                detail=str(exc))
        print(f"백업 실패 — 초기화하지 않고 중단: {exc}", file=sys.stderr)
        return 1

    # ── 초기화 → 배치 ───────────────────────────────────────────
    try:
        j.enter("reset_done", "reset")
        reset(root, profile, j, scope, mode, manifest)

        j.enter("installed", "deploy")
        plan = derive_plan(root, profile, plat)
        placed, conflicts = apply_plan(root, plan, j, scope,
                                        incremental=(mode == "incremental"),
                                        manifest=manifest)
        keys = apply_settings(root, profile, plan, j, scope, limits)

        j.enter("verified", "verify")
        vres = verify(root, profile, plan, j, clean_home=args.clean_home,
                      machine=mrec)
        if not vres["pass"]:
            raise RuntimeError(f"검증 불통과: {vres['failed']}")
    except Exception as exc:                      # noqa: BLE001
        j.write(phase="rolling_back", action="verify", outcome="fail",
                detail=str(exc))
        ok = restore(snap, j, root=root, scope=scope)
        j.write(phase="rolled_back" if ok else "failed_dirty", action="terminal",
                outcome="ok" if ok else "fail")
        if not ok:
            print("복원 실패 — 수동 복원 한 줄 명령:", file=sys.stderr)
            print(f"  cp -a {snap}/user_claude_config/. ~/ && "
                  f"echo RESTORED && ls -la ~/.claude", file=sys.stderr)
        print(f"설치 실패, 롤백 {'성공' if ok else '실패'}: {exc}", file=sys.stderr)
        return 1

    # ── 커밋 ────────────────────────────────────────────────────
    out_manifest = {
        "release_version": (root / "harness/VERSION").read_text().strip(),
        "installer_version": "0.1.0", "transaction_id": txn,
        "installed_at": clock.iso_local(), "machine_id": machine_id,
        "platform": plat, "snapshot_id": snap.name,
        "reset_mode": mode,
        "assets": [{"asset_id": p["asset_id"], "kind": p["kind"],
                    "version": p["decl"]["version"], "src_hash": p["src_hash"],
                    "dest_path": str(p["dest"]),
                    "dest_hash": sha256_file(p["dest"]) if p["dest"].exists() else None}
                   for p in placed],
        "settings_keys": keys,
        "verify_result": vres,
        "pending": conflicts,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(out_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    try:
        w = envelope.Writer(root_dir=root, stream="agent-events",
                            identity={"machine": machine_id, "session": txn,
                                      "plane": "interactive", "role": "devops-engineer",
                                      "req": None})
        w.append("install.committed", {"txn": txn, "assets": len(placed),
                                       "platform": plat})
    except Exception:                             # noqa: BLE001
        pass
    j.write(phase="committed", action="terminal", outcome="ok",
            detail=f"자산 {len(placed)} · 설정 키 {len(keys)}")

    print(f"설치 완료 — 트랜잭션 {txn}")
    print(f"  플랫폼      : {plat}")
    print(f"  자산        : {len(placed)}개 (에이전트 "
          f"{sum(1 for p in placed if p['kind']=='agent')} · 스킬 "
          f"{sum(1 for p in placed if p['kind']=='skill')} · 훅 "
          f"{sum(1 for p in placed if p['kind']=='hook')//2})")
    print(f"  동시성 상한 : {limits['concurrency_cap']} "
          f"(mem_available={probe.get('mem_available_mb')}MB · "
          f"cpu={probe.get('cpu_logical')})")
    print(f"  백업        : {snap}")
    print(f"  검증        : 통과 {vres['passed']} / 불통과 {vres['failed']} / "
          f"검증 불가 {vres['unverifiable']}")
    if conflicts:
        print(f"  보류        : {len(conflicts)}건 (사용자 커스텀 보존 — 덮지 않음)")
    orphans = find_orphans(root, profile, plan)
    if orphans:
        print(f"  고아 자산   : {len(orphans)}건 — 표에 없는 관리물이 배치 표면에 "
              f"남아 있습니다. 초기화하지 않는 것이 확정 동작이므로 지우지 않았고,")
        print(f"                제거하려면 --mode reset-managed 로 실행하십시오.")
        for o in orphans[:5]:
            print(f"                {o}")
    return 0


def cmd_uninstall(args) -> int:
    root = pathlib.Path(args.root).resolve()
    mpath = root / "config/install/installed-manifest.json"
    if not mpath.exists():
        print("매니페스트 부재 — 추측 제거를 하지 않는다(E§2.8)", file=sys.stderr)
        return 1
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    profile = load_profile(root, manifest["platform"])
    txn = uuid.uuid4().hex[:12]
    j = Journal(root, txn, "uninstall")
    j.write(phase="planned", action="write", outcome="ok")
    scope = Scope(root, profile, j)
    snapshot(root, profile, j, txn, trigger="uninstall")
    removed = 0
    for a in manifest["assets"]:
        p = pathlib.Path(a["dest_path"])
        if p.exists() and _touch(scope, j, "delete", p, p.unlink):
            removed += 1
    surface = root / profile["surfaces"]["project-settings"]
    if surface.exists():
        cfg = json.loads(surface.read_text(encoding="utf-8"))
        for k in {e["key"] for e in manifest["settings_keys"] if e.get("managed")}:
            cfg.pop(k, None)
        surface.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    os.replace(mpath, mpath.with_name(
        f"uninstalled-{clock.now_utc():%Y%m%dT%H%M%SZ}.json"))
    j.write(phase="committed", action="terminal", outcome="ok",
            detail=f"{removed}건 제거 · docs/·vault/·config/ 는 사용자 데이터라 보존")
    print(f"언인스톨 완료 — 자산 {removed}건 제거. "
          f"docs/·vault/·config/ 는 묻지 않고 보존했다(파괴적 조작의 기본값은 보존)")
    return 0


def cmd_rollback(args) -> int:
    root = pathlib.Path(args.root).resolve()
    snap = pathlib.Path(args.snapshot)
    if not (snap / "manifest.json").exists():
        print("매니페스트 없는 스냅샷은 완결분이 아니다", file=sys.stderr)
        return 1
    j = Journal(root, uuid.uuid4().hex[:12], "rollback")
    ok = restore(snap, j, root=root)
    j.write(phase="rolled_back" if ok else "failed_dirty", action="terminal",
            outcome="ok" if ok else "fail")
    print("복원 " + ("성공" if ok else "실패"))
    return 0 if ok else 1


def cmd_status(args) -> int:
    root = pathlib.Path(args.root).resolve()
    stale = unfinished_transactions(root)
    mpath = root / "config/install/installed-manifest.json"
    print(f"실행 루트    : {root}")
    print(f"플랫폼       : {detect_platform()}")
    print(f"미완 트랜잭션: {len(stale)}건")
    if mpath.exists():
        m = json.loads(mpath.read_text(encoding="utf-8"))
        print(f"설치 인스턴스: release={m['release_version']} txn={m['transaction_id']}"
              f" at={m['installed_at']}")
        print(f"  자산 {len(m['assets'])} · 설정 키 {len(m['settings_keys'])} · "
              f"보류 {len(m['pending'])}")
    else:
        print("설치 인스턴스: 없음")
    return 0


def main():
    ap = argparse.ArgumentParser(description="하네스 설치기 (E§2 트랜잭션)")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("install")
    p.add_argument("--mode", default=DEFAULT_RESET_MODE, choices=RESET_MODES,
                   help="기본은 reset-none — 설치기는 초기화하지 않는다")
    p.add_argument("--platform", default=None)
    p.add_argument("--alias", default=None)
    p.add_argument("--names", default=None, help="role_key→표시명 JSON 파일")
    p.add_argument("--clean-home", action="store_true")
    p.add_argument("--force-new", action="store_true")
    p.add_argument("--ack-deletion-list", action="store_true",
                   help="reset-full 전용 — 삭제 예정 목록을 열람했음을 기록한다")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("rollback")
    p.add_argument("--snapshot", required=True)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
