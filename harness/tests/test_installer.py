"""설치기 트랜잭션 계약의 실패 선행 테스트.

정본: E§2.5 배치 파생 · E§2.9 롤백 · E§2.6 검증 판정식 · E§2.10 저널

이 파일의 첫 세 테스트는 **실제 설치 실행에서 드러난 결함**에서 나왔다
(2026-07-29T09:52 트랜잭션 d55103625edc). 회귀 테스트는 수정 전에 실패를
증명해야 유효하다(S§9.5-33).
"""
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE / "installer"))

import install as I  # noqa: E402


def _lab():
    """실행 루트 골격 + 자산을 복사한 임시 랩."""
    lab = pathlib.Path(tempfile.mkdtemp(prefix="harness-inst-"))
    (lab / "harness").mkdir()
    for sub in ("assets", "installer", "lib"):
        shutil.copytree(HERE / sub, lab / "harness" / sub)
    (lab / "harness" / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    for c in ("requests", "audit", "design", "decisions", "failures",
              "minutes", "adjudications", "handoff", "reference"):
        (lab / "docs" / c / "public" / "2026" / "07").mkdir(parents=True)
        (lab / "docs" / c / "private" / "2026" / "07").mkdir(parents=True)
    (lab / ".claude").mkdir()
    (lab / "config" / "install" / "journal").mkdir(parents=True)
    return lab


def _profile(lab):
    p = json.loads((lab / "harness/installer/profiles/linux.json").read_text("utf-8"))
    p["backup_root"] = str(lab.parent / f"{lab.name}-backups")
    # 시험이 실사용자 홈을 백업 대상으로 삼지 않게 한다 — 느리고, 남의 데이터다
    p["user_home"] = str(lab / "fake-home")
    (lab / "fake-home" / ".claude").mkdir(parents=True, exist_ok=True)
    return p


# ── D1 — 배치 계획이 훅 실행체를 포함해야 한다 ──────────────────
def test_plan_includes_hook_executables_not_only_declarations():
    """실사고: hook.json 만 배치되고 run.py 가 빠져, 훅이 등록됐으나 실행체가
    없어 모든 도구 호출이 차단됐다."""
    lab = _lab()
    plan = I.derive_plan(lab, _profile(lab), "linux")
    hooks = [p for p in plan if p["kind"] == "hook"]
    names = {p["src"].name for p in hooks}
    assert "hook.json" in names
    assert "run.py" in names, f"훅 실행체가 배치 계획에 없다: {sorted(names)}"
    for p in hooks:
        if p["src"].name == "run.py":
            assert p["dest"].name == "run.py"
            assert p["dest"].parent.name == p["src"].parent.name


def test_every_registered_hook_command_has_an_existing_executable():
    """A8(신설) — 등록과 실행체는 한 단위다. 한쪽만 있으면 fail-closed 로
    전 도구 호출이 막힌다(실측된 플랫폼 성질)."""
    lab = _lab()
    prof = _profile(lab)
    plan = I.derive_plan(lab, prof, "linux")
    j = I.Journal(lab, "t-test", "install")
    scope = I.Scope(lab, prof, j)
    I.apply_plan(lab, plan, j, scope, incremental=False)
    I.apply_settings(lab, prof, plan, j, scope, {"concurrency_cap": 1})
    cfg = json.loads((lab / ".claude/settings.json").read_text("utf-8"))
    missing = []
    for _event, groups in cfg.get("hooks", {}).items():
        for g in groups:
            for h in g["hooks"]:
                script = h["command"].split()[-1]
                if not pathlib.Path(script).exists():
                    missing.append(script)
    assert missing == [], f"등록됐으나 실행체 부재: {missing}"


# ── D2 — 롤백은 추가분을 제거해야 한다 ──────────────────────────
def test_rollback_removes_paths_the_transaction_added():
    """실사고: 스냅샷 복사만으로는 새로 생긴 파일이 남는다. 남은 settings.json 이
    지워진 실행체를 가리켜 환경이 더 깊이 망가졌다."""
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-roll", "install")
    snap = I.snapshot(lab, prof, j, "t-roll", trigger="install")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    I.apply_plan(lab, plan, j, scope, incremental=False)
    I.apply_settings(lab, prof, plan, j, scope, {"concurrency_cap": 1})
    assert (lab / ".claude/settings.json").exists()
    assert list((lab / ".claude/agents").glob("*.md"))

    ok = I.restore(snap, j, root=lab, scope=scope)
    assert ok
    assert not (lab / ".claude/settings.json").exists(), \
        "트랜잭션이 만든 설정 표면이 롤백 후에도 남았다"
    assert not list((lab / ".claude/agents").glob("*.md")), \
        "배치된 에이전트 정의가 롤백 후에도 남았다"


def test_rollback_does_not_touch_foreign_files():
    """E§2.4.4 불가침 — 트랜잭션이 만들지 않은 것은 롤백도 건드리지 않는다."""
    lab = _lab()
    prof = _profile(lab)
    foreign = lab / ".claude" / "user-hand-written.json"
    foreign.write_text('{"mine": true}', encoding="utf-8")
    j = I.Journal(lab, "t-foreign", "install")
    snap = I.snapshot(lab, prof, j, "t-foreign", trigger="install")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    I.apply_plan(lab, plan, j, scope, incremental=False)
    I.restore(snap, j, root=lab, scope=scope)
    assert foreign.exists(), "롤백이 foreign 자산을 지웠다"
    assert json.loads(foreign.read_text("utf-8")) == {"mine": True}


# ── D3 — 제거 순서: 등록 표면이 실행체보다 먼저 ─────────────────
def test_removal_order_puts_settings_surface_before_executables():
    """등록이 남고 실행체가 사라지는 창을 만들지 않는다 — 그 창이 실사고의
    폭발 지점이었다."""
    order = I.REMOVAL_ORDER
    assert order.index("settings-fragment") < order.index("hook")
    assert order.index("hook") < order.index("agent")


# ── E§2.5 배치 파생 일반 ────────────────────────────────────────
def test_plan_is_derived_only_from_glob_and_declarations():
    lab = _lab()
    plan = I.derive_plan(lab, _profile(lab), "linux")
    kinds = {p["kind"] for p in plan}
    assert {"agent", "skill", "hook", "settings-fragment"} <= kinds
    assert len([p for p in plan if p["kind"] == "agent"]) == 14


def test_plan_rejects_unresolved_skill_reference():
    lab = _lab()
    bad = lab / "harness/assets/agents/lead.md"
    bad.write_text(bad.read_text("utf-8").replace(
        "skill.intake-classify", "skill.does-not-exist"), encoding="utf-8")
    try:
        I.derive_plan(lab, _profile(lab), "linux")
    except SystemExit:
        return
    raise AssertionError("미해결 참조는 배치 시작 전에 실패해야 한다")


def test_platform_mismatch_assets_are_not_planned():
    lab = _lab()
    only_win = lab / "harness/assets/settings/win-only.json"
    only_win.write_text(json.dumps({"declaration": {
        "asset_id": "settings.win-only", "asset_kind": "settings-fragment",
        "version": "0.1.0", "platforms": ["windows-native"], "scope": "project",
        "managed_mark": I.MANAGED_MARK, "target_surface": "project-settings",
        "merge_strategy": "set-if-absent", "keys": {}}}), encoding="utf-8")
    plan = I.derive_plan(lab, _profile(lab), "linux")
    assert "settings.win-only" not in {p["asset_id"] for p in plan}


# ── E§2.4 초기화 범위 ───────────────────────────────────────────
def test_reset_managed_never_deletes_unmarked_files():
    lab = _lab()
    prof = _profile(lab)
    (lab / ".claude/agents").mkdir(parents=True, exist_ok=True)
    foreign = lab / ".claude/agents/mine.md"
    foreign.write_text("표식 없는 사용자 자산", encoding="utf-8")
    j = I.Journal(lab, "t-reset", "reset-managed")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    I.apply_plan(lab, plan, j, scope, incremental=False)
    I.reset(lab, prof, j, scope, "reset-managed", None)
    assert foreign.exists(), "reset-managed 가 foreign 을 지웠다"
    assert not list((lab / ".claude/agents").glob("*-developer.md"))


def test_reset_full_is_refused_without_decision_record():
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-full", "reset-full")
    scope = I.Scope(lab, prof, j)
    try:
        I.reset(lab, prof, j, scope, "reset-full", None)
    except SystemExit:
        return
    raise AssertionError("reset-full 은 결정 착지 전에는 실행 불가여야 한다")


# ── E§2.3 백업 ──────────────────────────────────────────────────
def test_backup_root_inside_execution_root_is_refused():
    lab = _lab()
    prof = _profile(lab)
    prof["backup_root"] = str(lab / "inside")
    j = I.Journal(lab, "t-bk", "install")
    try:
        I.snapshot(lab, prof, j, "t-bk", trigger="install")
    except SystemExit:
        return
    raise AssertionError("백업 루트가 실행 루트 하위면 자기 파괴 경로다")


def test_manifest_presence_is_the_completion_predicate():
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-man", "install")
    snap = I.snapshot(lab, prof, j, "t-man", trigger="install")
    assert (snap / "manifest.json").exists()
    assert not snap.name.endswith(".partial")
    man = json.loads((snap / "manifest.json").read_text("utf-8"))
    assert man["verify"]["hash_verified"] == man["verify"]["entries_total"]


# ── E§2.10 저널 ─────────────────────────────────────────────────
def test_journal_records_are_one_per_line():
    lab = _lab()
    j = I.Journal(lab, "t-j", "install")
    j.write(step="a", detail="여러\n줄\n값")
    lines = j.path.read_text("utf-8").splitlines()
    assert len(lines) == 1
    json.loads(lines[0])


def test_unfinished_transaction_is_detected():
    lab = _lab()
    j = I.Journal(lab, "t-open", "install")
    j.write(phase="planned", step="open")
    assert len(I.unfinished_transactions(lab)) == 1
    j.write(phase="committed", action="terminal")
    assert I.unfinished_transactions(lab) == []


def test_scope_gate_blocks_paths_outside_whitelist():
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-scope", "install")
    scope = I.Scope(lab, prof, j)
    assert scope.check(lab / ".claude/agents/x.md", write=True) is True
    assert scope.check(lab / "docs/design/public/2026/07/x.md", write=True) is False
    assert scope.check(pathlib.Path("/etc/passwd"), write=True) is False
    assert '"scope_violation"' in j.path.read_text("utf-8")


# ── D5 — 스냅샷은 자기 트랜잭션의 진행 중 파일에 걸리지 않는다 ──
def test_snapshot_hash_verification_survives_a_moving_source():
    """실사고: 백업이 `config/install/journal/<현 txn>.jsonl` 을 뜨는데 그 파일은
    백업이 도는 동안에도 append 된다. 소스 기준 해시는 복사 직후 어긋나고
    `백업 검증 실패 — 460/484` 로 트랜잭션이 죽는다.

    검증의 대상은 **보관본의 무결성**이어야 한다 — 복사 시점에 이미 변하고 있던
    소스와의 일치는 원리적으로 보장 불가능하고, 복원이 쓰는 것은 보관본이다.
    """
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-move", "install")
    j.write(step="before-snapshot")

    live = lab / "config" / "moving.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("line1\n", encoding="utf-8")

    orig = I.sha256_file

    def hash_then_move(p):
        h = orig(p)
        # **소스만** 변형한다. 보관본까지 건드리면 시험이 재현하려는 상황
        # (소스가 움직인다)이 아니라 다른 상황(보관본이 손상된다)이 된다.
        if pathlib.Path(p) == live:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write("line-appended-during-snapshot\n")
        return h

    I.sha256_file = hash_then_move
    try:
        snap = I.snapshot(lab, prof, j, "t-move", trigger="install")
    finally:
        I.sha256_file = orig

    man = json.loads((snap / "manifest.json").read_text("utf-8"))
    assert man["verify"]["hash_verified"] == man["verify"]["entries_total"]


def test_snapshot_excludes_current_transaction_journal():
    """자기 트랜잭션의 저널은 백업 대상이 아니다 — 백업의 기록을 백업이 뜨는
    자기참조이고, 복원해도 의미가 없다."""
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-self", "install")
    j.write(step="open")
    snap = I.snapshot(lab, prof, j, "t-self", trigger="install")
    man = json.loads((snap / "manifest.json").read_text("utf-8"))
    archived = {e["source_path"] for e in man["entries"]}
    assert str(j.path) not in archived
    assert any(e["reason"].startswith("현 트랜잭션") for e in man["excluded"])


def test_archived_paths_are_collision_free():
    """실사고: 보관 경로 폴백이 파일 **이름만** 써서 27개 파일이 3개 경로로
    겹쳤고 24건이 조용히 유실됐다. 백업의 무유실은 이 성질에 걸려 있다."""
    lab = _lab()
    prof = _profile(lab)
    # 같은 basename 을 여러 디렉토리에 만든다 — 훅 자산의 실제 모양이다
    for name in ("a", "b", "c"):
        d = lab / ".claude" / "hooks" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "hook.json").write_text(f'{{"n":"{name}"}}', encoding="utf-8")
        (d / "run.py").write_text(f"# {name}\n", encoding="utf-8")
    j = I.Journal(lab, "t-coll", "install")
    snap = I.snapshot(lab, prof, j, "t-coll", trigger="install")
    man = json.loads((snap / "manifest.json").read_text("utf-8"))
    archived = [e["archived_path"] for e in man["entries"]]
    assert len(archived) == len(set(archived)), \
        f"보관 경로 충돌 {len(archived) - len(set(archived))}건"
    assert man["verify"]["hash_verified"] == man["verify"]["entries_total"]
    # 원본과 보관본의 내용이 파일마다 보존됐는지 실물로 확인한다
    for e in man["entries"]:
        src, dst = pathlib.Path(e["source_path"]), snap / e["archived_path"]
        assert dst.read_bytes() == src.read_bytes(), e["source_path"]


def test_hook_executables_carry_the_managed_mark():
    """실사고: 훅 실행체에 관리 표식이 없어 ①증분 설치가 '사용자 커스텀'으로
    보류해 갱신되지 않고 ②reset-managed 가 관리물로 식별하지 못해 남는다.

    표식은 선언 블록이 아니다 — 선언은 hook.json 에만 두고(E§2.5.1), 실행체에는
    식별용 고정 문자열만 둔다. 둘을 혼동하면 파서가 언어별로 갈라진다."""
    lab = _lab()
    for run in (lab / "harness/assets/hooks").glob("*/run.py"):
        assert I.MANAGED_MARK in run.read_text(encoding="utf-8"), run


def test_incremental_updates_changed_hook_executable():
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-inc", "install")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    I.apply_plan(lab, plan, j, scope, incremental=False)
    src = next(p for p in plan if p["src"].name == "run.py")
    src["src"].write_text(src["src"].read_text("utf-8") + "\n# changed\n",
                          encoding="utf-8")
    plan2 = I.derive_plan(lab, prof, "linux")
    placed, conflicts = I.apply_plan(lab, plan2, j, scope, incremental=True)
    assert conflicts == [], f"관리물이 충돌로 보류됐다: {conflicts}"
    assert "# changed" in src["dest"].read_text("utf-8")


def test_incremental_uses_manifest_join_not_only_the_mark():
    """실사고: 표식을 도입하기 **전에** 배치된 파일은 표식이 없다. 증분 판정이
    표식만 보면 설치기가 자기가 놓은 파일을 '사용자 커스텀'으로 보류하고,
    수정본이 영원히 배치되지 않는다(실측 2026-07-29T11:29 — 보류 5건 반복).

    E§2.4.1 의 클래스 판정식은 표식 **또는 매니페스트 조인**이다. 증분 경로가
    그 둘 중 하나만 쓰면 판정식이 절반만 구현된 것이다."""
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-join", "install")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    I.apply_plan(lab, plan, j, scope, incremental=False)

    target = next(p for p in plan if p["kind"] == "agent")
    # 표식 도입 이전 배치본을 모사한다 — 내용은 다르고 표식은 없다
    target["dest"].write_text("표식 없는 구판 배치본\n", encoding="utf-8")
    manifest = {"assets": [{"asset_id": p["asset_id"], "dest_path": str(p["dest"])}
                           for p in plan]}

    placed, conflicts = I.apply_plan(lab, plan, j, scope, incremental=True,
                                     manifest=manifest)
    assert conflicts == [], f"매니페스트에 있는 자산이 충돌로 보류됐다: {conflicts}"
    assert I.MANAGED_MARK in target["dest"].read_text("utf-8")


def test_incremental_still_preserves_truly_foreign_files():
    """매니페스트에도 없고 표식도 없으면 보존한다 — 조인 도입이 불가침을 깨지 않는다."""
    lab = _lab()
    prof = _profile(lab)
    j = I.Journal(lab, "t-join2", "install")
    scope = I.Scope(lab, prof, j)
    plan = I.derive_plan(lab, prof, "linux")
    target = next(p for p in plan if p["kind"] == "agent")
    target["dest"].parent.mkdir(parents=True, exist_ok=True)
    target["dest"].write_text("사용자가 손으로 쓴 정의\n", encoding="utf-8")
    placed, conflicts = I.apply_plan(lab, plan, j, scope, incremental=True,
                                     manifest={"assets": []})
    assert any(c["asset_id"] == target["asset_id"] for c in conflicts)
    assert target["dest"].read_text("utf-8") == "사용자가 손으로 쓴 정의\n"
