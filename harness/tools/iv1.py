#!/usr/bin/env python3
"""IV1 — 릴리스 게이트: 청정 디렉토리 실설치 + 기동 검증 + 멱등 + 복원 리허설.

정본: E§2.6(검증 단계 두 겹 중 IV1) · E§3.1(멱등 판정식) · E§8.3(패키징 게이트).

실환경(IV2)보다 **먼저** 돈다. 근거는 이 하네스 자신의 실사고다: 실환경에서
검증 불통과 → 롤백이 불완전 → 도구 호출 전면 차단으로 이어졌다. 청정 랩에서
같은 일이 나면 랩만 버리면 된다.

판정 축
  G1 참조 그래프 전건 해소 + asset_id 정규화 중복 0
  G2 청정 루트 실설치 성공 + verify_pass 전 축 통과
  G3 멱등 — 2회 연속 설치의 관리물 해시 집합 동일
  G4 스냅샷 복원 리허설 — 롤백 후 설치 전 상태로 수렴(추가분 잔존 0)
  G5 언인스톨 — 매니페스트 자산 제거 후 사용자 데이터 보존
종료 코드 0 = 전건 통과 · 1 = 불통과 · 2 = 실행 오류.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))
sys.path.insert(0, str(ROOT / "harness" / "installer"))

import install as I  # noqa: E402
from harness_core import clock, paths  # noqa: E402

CATEGORIES = paths.CATEGORIES


def build_clean_root() -> pathlib.Path:
    lab = pathlib.Path(tempfile.mkdtemp(prefix="harness-iv1-"))
    (lab / "harness").mkdir()
    for sub in ("assets", "installer", "lib", "tools"):
        shutil.copytree(ROOT / "harness" / sub, lab / "harness" / sub)
    shutil.copy2(ROOT / "harness" / "VERSION", lab / "harness" / "VERSION")
    for c in CATEGORIES:
        for v in ("public", "private"):
            (lab / "docs" / c / v / "2026" / "07").mkdir(parents=True)
    for d in ("config/local", "config/names", "config/machines",
              "config/install/journal", "vault", "derived", ".claude",
              "fake-home/.claude"):
        (lab / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / ".gitignore", lab / ".gitignore")
    return lab


def managed_state(lab: pathlib.Path) -> dict[str, str]:
    """관리물 집합의 경로→해시. 멱등 비교의 대상이다.

    `foreign` 클래스는 비교 밖이다 — reset-managed 에서 불가촉이므로
    불변이 곧 요구사항이고, 비교에 넣으면 요구를 검사가 아니라 우연으로 만든다.
    """
    out = {}
    for f in sorted((lab / ".claude").rglob("*")):
        if f.is_file():
            try:
                if I.MANAGED_MARK in f.read_text(encoding="utf-8", errors="ignore"):
                    out[str(f.relative_to(lab))] = I.sha256_file(f)
            except OSError:
                pass
    s = lab / ".claude/settings.json"
    if s.exists():
        out["settings.json"] = I.sha256_file(s)
    return out


def run_install(lab: pathlib.Path, *extra) -> tuple[int, str]:
    cmd = [sys.executable, str(lab / "harness/installer/install.py"),
           "--root", str(lab), "install", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return p.returncode, (p.stdout + p.stderr)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    lab = build_clean_root()
    print(f"청정 루트: {lab}")

    # ── G1 참조 그래프·ID 정규화 ────────────────────────────────
    prof = json.loads(
        (lab / "harness/installer/profiles/windows-wsl.json").read_text("utf-8"))
    prof_path = lab / "harness/installer/profiles/windows-wsl.json"
    prof["backup_root"] = str(lab.parent / f"{lab.name}-backups")
    prof_path.write_text(json.dumps(prof, ensure_ascii=False, indent=2), "utf-8")
    for other in (lab / "harness/installer/profiles").glob("*.json"):
        d = json.loads(other.read_text("utf-8"))
        d["backup_root"] = prof["backup_root"]
        d["user_home"] = str(lab / "fake-home")
        other.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    try:
        plan = I.derive_plan(lab, prof, "windows-wsl")
        ids_lower = [p["asset_id"].lower() for p in plan]
        dup = len(ids_lower) != len(set(ids_lower))
        results.append(("G1 참조 그래프 해소 + asset_id 중복 0", not dup,
                        f"자산 {len(plan)}개 · 소문자 정규화 중복="
                        f"{'있음' if dup else '없음'}"))
    except SystemExit as exc:
        results.append(("G1 참조 그래프 해소", False, str(exc)))
        plan = []

    # ── G2 청정 루트 실설치 + 기동 검증 ─────────────────────────
    rc, out = run_install(lab, "--alias", "iv1-lab")
    manifest_path = lab / "config/install/installed-manifest.json"
    ok2 = rc == 0 and manifest_path.exists()
    detail = out.strip().splitlines()[-1] if out.strip() else f"exit={rc}"
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text("utf-8"))
        v = m["verify_result"]
        detail = (f"통과={v['passed']} 불통과={v['failed']} "
                  f"검증불가={v['unverifiable']}")
        ok2 = ok2 and v["pass"]
    results.append(("G2 청정 루트 실설치 + 기동 검증", ok2, detail))
    if not ok2:
        print(out[-3000:])

    # ── G3 멱등 ─────────────────────────────────────────────────
    if ok2:
        first = managed_state(lab)
        rc2, out2 = run_install(lab, "--alias", "iv1-lab")
        second = managed_state(lab)
        same = first == second
        results.append(("G3 멱등 — 2회 연속 설치 해시 집합 동일", same and rc2 == 0,
                        f"1회차 {len(first)}개 · 2회차 {len(second)}개 · "
                        f"차분 {len(set(first.items()) ^ set(second.items()))}"))
    else:
        results.append(("G3 멱등", False, "G2 불통과로 미실행"))

    # ── G4 롤백 리허설 — 추가분 제거 + 복원 ─────────────────────
    lab2 = build_clean_root()
    for other in (lab2 / "harness/installer/profiles").glob("*.json"):
        d = json.loads(other.read_text("utf-8"))
        d["backup_root"] = str(lab2.parent / f"{lab2.name}-backups")
        d["user_home"] = str(lab2 / "fake-home")
        other.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    prof2 = json.loads(
        (lab2 / "harness/installer/profiles/windows-wsl.json").read_text("utf-8"))
    foreign = lab2 / ".claude/foreign-user-file.json"
    foreign.write_text('{"user": "keep me"}', encoding="utf-8")
    j = I.Journal(lab2, "iv1roll", "install")
    snap = I.snapshot(lab2, prof2, j, "iv1roll", trigger="install")
    scope = I.Scope(lab2, prof2, j)
    plan2 = I.derive_plan(lab2, prof2, "windows-wsl")
    I.apply_plan(lab2, plan2, j, scope, incremental=False)
    I.apply_settings(lab2, prof2, plan2, j, scope, {"concurrency_cap": 1})
    before_rollback = sorted(p.name for p in (lab2 / ".claude").rglob("*")
                             if p.is_file())
    restored = I.restore(snap, j, root=lab2, scope=scope)
    after = sorted(str(p.relative_to(lab2)) for p in (lab2 / ".claude").rglob("*")
                   if p.is_file())
    leftovers = [a for a in after
                 if a not in (".claude/foreign-user-file.json",)]
    ok4 = restored and not leftovers and foreign.exists()
    results.append(("G4 롤백 리허설 — 추가분 제거 + foreign 보존", ok4,
                    f"배치 {len(before_rollback)}개 → 잔존 {leftovers} · "
                    f"foreign 보존={foreign.exists()}"))

    # ── G5 언인스톨 ─────────────────────────────────────────────
    if ok2:
        p = subprocess.run(
            [sys.executable, str(lab / "harness/installer/install.py"),
             "--root", str(lab), "uninstall"],
            capture_output=True, text=True, timeout=600)
        remain = [f for f in (lab / ".claude").rglob("*")
                  if f.is_file() and I.MANAGED_MARK in
                  f.read_text(encoding="utf-8", errors="ignore")]
        data_kept = all((lab / d).exists() for d in ("docs", "vault", "config"))
        ok5 = p.returncode == 0 and not remain and data_kept
        results.append(("G5 언인스톨 — 자산 제거 · 사용자 데이터 보존", ok5,
                        f"잔존 관리물 {len(remain)}개 · docs·vault·config 보존="
                        f"{data_kept}"))
    else:
        results.append(("G5 언인스톨", False, "G2 불통과로 미실행"))

    print(f"\n{'='*72}")
    print(f"IV1 릴리스 게이트 — {clock.iso_local()}")
    print("=" * 72)
    passed = 0
    for name, ok, detail in results:
        print(f"  [{'통과' if ok else '불통과'}] {name}")
        print(f"          {detail}")
        passed += bool(ok)
    print(f"\n{passed}/{len(results)} 통과   랩: {lab} · {lab2}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
