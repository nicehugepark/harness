"""오딧 봉투 조립기·레코드 원자 append 계약의 실패 선행 테스트.

정본: D§2.2 공통 레코드 봉투 · D§2.3 R38 레코드 원자 append 계약
      · A§2.4 기계 스트림 파일 계약 · D§2.6 상태형/사건형 분리
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import envelope, ids  # noqa: E402

IDENT = {
    "machine": "m01-wsl", "session": "s-test", "plane": "workflow",
    "req": "RQ-20260728T170616Z-d0bc4127", "role": "qa", "agent": "테스트작성자",
}


def _tmp():
    return pathlib.Path(tempfile.mkdtemp(prefix="harness-env-"))


def _writer(root, stream="agent-events"):
    return envelope.Writer(root_dir=root, stream=stream, identity=IDENT)


# ── D§2.1 스트림 레지스트리 ──────────────────────────────────────
def test_stream_registry_is_the_seven():
    assert set(envelope.STREAMS) == {
        "agent-events", "workflow-journal", "incidents", "notify",
        "telemetry", "recorder-health", "sched-decisions",
    }


def test_unregistered_stream_is_rejected_fail_closed():
    """R23 — 미등재 스트림명의 착지는 fail-closed 거부."""
    try:
        envelope.Writer(root_dir=_tmp(), stream="made-up", identity=IDENT)
    except ValueError:
        return
    raise AssertionError("unregistered stream must be rejected")


# ── D§2.2 봉투 ──────────────────────────────────────────────────
def test_envelope_carries_required_fields():
    w = _writer(_tmp())
    rec = w.build("tool.done", {"tool": "Read", "ok": True})
    for f in ["id", "ts", "stream", "event", "plane", "machine", "session", "data"]:
        assert f in rec, f
    assert rec["stream"] == "agent-events"
    assert rec["req"] == IDENT["req"]


def test_envelope_id_is_unique_under_same_timestamp():
    """D§2.2 — writer_pid·proc_nonce 성분이 동시 훅 프로세스의 id 충돌을 막는다."""
    w = _writer(_tmp())
    got = {w.build("x.y", {})["id"] for _ in range(3000)}
    assert len(got) == 3000


def test_missing_identity_field_diverts_to_recorder_health():
    """R26 — 봉투 필수 필드 결손 레코드는 본류에 착지하지 않는다."""
    root = _tmp()
    bad = dict(IDENT)
    del bad["machine"]
    w = envelope.Writer(root_dir=root, stream="agent-events", identity=bad)
    r = w.append("tool.done", {})
    assert r.diverted is True
    assert r.stream == "recorder-health"


# ── D§2.3 R38 레코드 원자 append 계약 ────────────────────────────
def test_record_size_cap_is_the_contract_constant():
    assert envelope.RECORD_MAX_BYTES == 4096


def test_oversize_record_spills_and_lands_within_cap():
    """R38-3 — 상한 초과분은 본류에 싣지 않고 스필 참조로 치환한다."""
    root = _tmp()
    w = _writer(root)
    r = w.append("tool.done", {"blob": "x" * 20000})
    assert r.ok
    line = pathlib.Path(r.path).read_text(encoding="utf-8").strip().splitlines()[-1]
    assert len(line.encode()) <= envelope.RECORD_MAX_BYTES
    rec = json.loads(line)
    assert rec["data"]["oversize"] is True
    assert rec["data"]["spill_ref"]
    assert pathlib.Path(root, rec["data"]["spill_ref"]).exists()


def test_every_line_is_exactly_one_record_even_with_newlines():
    """D§2.2 — 1레코드 = 1물리 줄. 문자열 내 개행은 직렬화기가 이스케이프한다."""
    root = _tmp()
    w = _writer(root)
    w.append("x.y", {"text": "첫 줄\n둘째 줄\n셋째 줄"})
    w.append("x.y", {"text": "또 다른\n값"})
    lines = pathlib.Path(w.current_path()).read_text(encoding="utf-8").splitlines()
    body = [ln for ln in lines if ln.strip()]
    assert len(body) == 3          # 헤더 1 + 레코드 2
    for ln in body:
        json.loads(ln)


def test_concurrent_append_keeps_line_count(tmp=None):
    """D§2.3 경합 해머 시험의 축소판 — N 프로세스 × M 회."""
    root = _tmp()
    n, m = 8, 60
    ok = envelope.hammer_test(root, processes=n, per_process=m)
    assert ok.lines == n * m, ok
    assert ok.parse_failures == 0
    assert ok.id_losses == 0


# ── A§2.4 파일 계약 ─────────────────────────────────────────────
def test_header_record_declares_the_four_contracts():
    root = _tmp()
    w = _writer(root)
    w.append("x.y", {})
    head = json.loads(
        pathlib.Path(w.current_path()).read_text(encoding="utf-8").splitlines()[0]
    )
    for f in ["stream", "producer", "consumer", "transition", "retention",
              "writer", "prev", "schema"]:
        assert f in head, f
    assert head["stream"] == "agent-events"
    assert head["prev"] is None


def test_stream_file_lands_directly_in_shard_with_au_id():
    root = _tmp()
    w = _writer(root)
    w.append("x.y", {})
    rel = pathlib.Path(w.current_path()).relative_to(root).as_posix()
    assert rel.startswith("docs/audit/public/")
    name = rel.rsplit("/", 1)[-1]
    assert name.startswith("AU-") and name.endswith(".jsonl")
    assert ids.parse(name.split("-agent-events")[0]).type_code == "AU"


def test_rotation_chains_via_prev_not_part_suffix():
    root = _tmp()
    w = _writer(root)
    w.append("x.y", {})
    first = w.current_path()
    w.rotate(reason="test")
    w.append("x.y", {})
    second = w.current_path()
    assert first != second
    head2 = json.loads(
        pathlib.Path(second).read_text(encoding="utf-8").splitlines()[0])
    assert head2["prev"] is not None
    assert "part" not in pathlib.Path(second).name


def test_incident_streams_land_in_failures_category():
    root = _tmp()
    w = envelope.Writer(root_dir=root, stream="incidents", identity=IDENT)
    w.append("incident.opened", {"key": "k"})
    rel = pathlib.Path(w.current_path()).relative_to(root).as_posix()
    assert rel.startswith("docs/failures/public/")
    assert rel.rsplit("/", 1)[-1].startswith("FS-")


# ── D§2.6 상태형/사건형 분리 ─────────────────────────────────────
def test_diff_emitter_only_emits_new_keys():
    root = _tmp()
    e = envelope.DiffEmitter(root_dir=root, axis="lint", machine="m01-wsl",
                             identity=IDENT)
    a = e.observe({"k1", "k2"})
    assert sorted(a.opened) == ["k1", "k2"] and a.resolved == []
    b = e.observe({"k1", "k2"})
    assert b.opened == [] and b.resolved == []      # 재탐지는 사건이 아니다
    c = e.observe({"k2", "k3"})
    assert c.opened == ["k3"] and c.resolved == ["k1"]


def test_incident_id_folds_repeat_detections():
    a = envelope.incident_key_id("m01-wsl", "lint", "slot-missing")
    b = envelope.incident_key_id("m01-wsl", "lint", "slot-missing")
    assert a == b and a.startswith("FI-")


# ── 시스템 생산자(요청에 속하지 않는 발신원) ────────────────────
def test_system_plane_does_not_require_request_identity():
    """스케줄러 틱·프로브는 **요청 실행이 아니다** — req·role 이 원리적으로 없다.

    D§2.2 는 `plane ≠ interactive` 이면 req·role 을 필수로 걸었는데, 그 조건은
    plane enum 이 workflow·loop·interactive 3값인 전제에서 쓰였다. 시스템
    발신원이 그 3값 어디에도 안 맞아 결정 로그가 통째로 recorder-health 로
    우회됐다(실측 2026-07-29T03:17 — 틱 로그 2건 우회).

    빈 자리를 메우는 방향은 두 가지였다: ①req 필수 조건을 푼다 ②시스템 평면을
    신설한다. ①은 요청 실행 레코드의 주체 미상까지 허용하므로 기각했다 —
    "누가"를 답하지 못하는 기록은 재발 방지에 쓸 수 없다.
    """
    root = _tmp()
    ident = {"machine": "m01", "session": "tick-1", "plane": "system"}
    w = envelope.Writer(root_dir=root, stream="sched-decisions", identity=ident)
    r = w.append("sched.tick", {"tick_id": "abc"})
    assert r.ok and r.diverted is False and r.stream == "sched-decisions"


def test_workflow_plane_still_requires_request_identity():
    root = _tmp()
    ident = {"machine": "m01", "session": "s1", "plane": "workflow"}   # req 없음
    w = envelope.Writer(root_dir=root, stream="agent-events", identity=ident)
    assert w.append("x.y", {}).diverted is True


def test_plane_enum_is_closed_and_rejects_unknown():
    root = _tmp()
    try:
        envelope.Writer(root_dir=root, stream="agent-events",
                        identity={"machine": "m", "session": "s", "plane": "made-up"})
    except ValueError:
        return
    raise AssertionError("plane enum 밖 값은 거부해야 한다")
