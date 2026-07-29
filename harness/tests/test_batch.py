"""배치 잡·통지 계약의 실패 선행 테스트.

정본: A§5.3 착지 2단계와 배치 커밋 · A§5.4-⑤ 배치 잡 · A§9 백업 대상
      · D§4 의사결정 문서 · D§5 3채널 동시 통지 · D§6 에스컬레이션 체커
"""
import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import batch as B, notify as N  # noqa: E402


def _repo():
    d = pathlib.Path(tempfile.mkdtemp(prefix="harness-batch-"))
    for c in ("requests", "audit", "design", "decisions", "adjudications",
              "failures"):
        (d / "docs" / c / "public" / "2026" / "07").mkdir(parents=True)
        (d / "docs" / c / "private" / "2026" / "07").mkdir(parents=True)
    (d / "config/local").mkdir(parents=True)
    (d / "derived/index").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "t"], check=True)
    (d / ".gitignore").write_text("/config/\n/derived/\ndocs/*/private/\n",
                                  encoding="utf-8")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "init"], check=True)
    return d


# ── A§5.3 배치 커밋 ─────────────────────────────────────────────
def test_batch_commit_lands_staged_documents():
    d = _repo()
    f = d / "docs/design/public/2026/07/DS-x.md"
    f.write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(d), "add", str(f)], check=True)
    out = B.batch_commit(d)
    assert out["committed"] is True
    assert B.tracked(d, "docs/design/public/2026/07/DS-x.md")


def test_batch_commit_is_idempotent_with_nothing_staged():
    d = _repo()
    out = B.batch_commit(d)
    assert out["committed"] is False and out["reason"] == "스테이징 없음"


def test_commit_existence_is_verified_not_assumed():
    """A§5.3 — 커밋 후 검증 잡이 '스테이징·커밋 실재'를 대조한다.
    반환값을 보존의 증거로 읽지 않는다."""
    d = _repo()
    f = d / "docs/design/public/2026/07/DS-y.md"
    f.write_text("y", encoding="utf-8")
    subprocess.run(["git", "-C", str(d), "add", str(f)], check=True)
    B.batch_commit(d)
    assert B.verify_commit_reality(d)["missing"] == []


# ── A§3.3-S3 미추적 검사 ────────────────────────────────────────
def test_untracked_public_document_is_a_defect_state():
    """실사고 클래스: 자동 커밋 경로가 신규 문서를 흡수하지 못해 확정 결정문이
    미추적으로 방치됐다."""
    d = _repo()
    (d / "docs/decisions/public/2026/07/DN-z.md").write_text("z", encoding="utf-8")
    found = B.find_untracked(d)
    assert "docs/decisions/public/2026/07/DN-z.md" in found


def test_private_tree_is_not_reported_as_untracked():
    d = _repo()
    (d / "docs/decisions/private/2026/07/DN-p.md").write_text("p", encoding="utf-8")
    assert B.find_untracked(d) == []


def test_stray_tmp_files_are_swept():
    d = _repo()
    t = d / "docs/design/public/2026/07/DS-a.md.tmp"
    t.write_text("partial", encoding="utf-8")
    n = B.sweep_tmp(d)
    assert n == 1 and not t.exists()


# ── A§7.3 인덱스 컴팩션 ─────────────────────────────────────────
def test_index_compaction_keeps_last_record_per_id():
    d = _repo()
    p = d / "derived/index/docs.jsonl"
    p.write_text("\n".join([
        json.dumps({"id": "A", "state": "received"}),
        json.dumps({"id": "B", "state": "queued"}),
        json.dumps({"id": "A", "state": "done"}),
    ]) + "\n", encoding="utf-8")
    B.compact_index(p)
    recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 2
    assert next(r for r in recs if r["id"] == "A")["state"] == "done"


def test_index_compaction_treats_unparseable_line_as_regeneration_trigger():
    d = _repo()
    p = d / "derived/index/docs.jsonl"
    p.write_text('{"id":"A"}\n깨진 줄\n', encoding="utf-8")
    out = B.compact_index(p)
    assert out["regenerate"] is True


# ── D§4 의사결정 문서 · D§5 3채널 ───────────────────────────────
def test_adjudication_requires_question_reason_and_at_least_one_option():
    ok, why = N.validate_adjudication({"question": "", "reason": "hitl-gate",
                                       "options": []})
    assert not ok


def test_adjudication_reason_enum_is_closed():
    ok, why = N.validate_adjudication({"question": "q", "reason": "made-up",
                                       "options": [{"oid": "a", "label": "A"}]})
    assert not ok and "reason" in why


def test_three_channels_fire_simultaneously_not_as_fallback():
    """D§5.1 — 폴백이 아니다. 수신 단말 존부를 판정하지 않는다."""
    sent = []
    res = N.notify({"id": "AJ-x", "question": "q"}, level=0,
                   channels={"terminal": lambda m: sent.append("terminal") or True,
                             "mobile": lambda m: sent.append("mobile") or False},
                   append=lambda rec: sent.append("dashboard"))
    assert set(sent) == {"terminal", "mobile", "dashboard"}
    assert res["delivered"]["terminal"] is True
    assert res["delivered"]["mobile"] is False       # 미전달도 기록한다


def test_notify_has_no_retry_loop():
    """R31 — 채널 내 재시도·채널 간 폴백이 없다. 회수는 에이징이 담당한다."""
    calls = []
    N.notify({"id": "AJ-x", "question": "q"}, level=0,
             channels={"terminal": lambda m: calls.append(1) or False},
             append=lambda rec: None)
    assert len(calls) == 1


def test_notify_body_carries_no_values_or_credentials():
    """R34 — 통지 본문에 값·자격증명·실주소를 싣지 않는다."""
    msg = N.render_short({"id": "AJ-x", "question": "포트를 열까요",
                          "options": [{"oid": "a", "label": "예"}],
                          "deadline": "2026-07-30T00:00:00Z",
                          "path": "docs/adjudications/public/2026/07/AJ-x.md",
                          "secret": "password: hunter2"})
    assert "hunter2" not in msg and "password" not in msg
    assert "AJ-x" in msg and "포트를 열까요" in msg


def test_deadline_never_auto_executes_an_option():
    """미응답 시 자동 집행 금지 — 스키마에 기본 집행 필드 자체가 없다."""
    assert "default_execute" not in N.ADJUDICATION_FIELDS
    assert "auto_apply" not in N.ADJUDICATION_FIELDS


# ── D§6.2 에스컬레이션 체커 ─────────────────────────────────────
def test_escalation_raises_level_and_reschedules_without_new_document():
    """R28 — 재통지는 같은 문서의 level 상승이지 새 문서·새 사건이 아니다."""
    adj = {"id": "AJ-x", "state": "notified",
           "escalation": {"level": 0, "next_check_at": "2026-07-29T00:00:00Z"}}
    out = N.escalate(adj, now="2026-07-29T01:00:00Z")
    assert out["escalation"]["level"] == 1
    assert out["id"] == "AJ-x" and out.get("new_document") is None


def test_escalation_marks_request_blocked_on_human_at_level_two():
    adj = {"id": "AJ-x", "state": "notified", "refs": {"request": "RQ-x"},
           "escalation": {"level": 1, "next_check_at": "2026-07-29T00:00:00Z"}}
    out = N.escalate(adj, now="2026-07-29T05:00:00Z")
    assert out["escalation"]["level"] == 2
    assert out["side_effects"]["blocked_on"] == "human"


def test_backoff_ladder_is_monotonic():
    ladder = [N.backoff_minutes(l) for l in range(1, 6)]
    assert ladder == sorted(ladder) and ladder[0] == 30
