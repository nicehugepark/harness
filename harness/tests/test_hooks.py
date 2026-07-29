"""리드 게이트 훅 판정의 실패 선행 테스트.

정본: F§3.2 착지 판정식 · X8 응답 머리 표기 게이트 · F§7.9 상찬 어휘 차단

`test_stop_input_prefers_last_assistant_message` 는 **실사고**에서 나왔다
(2026-07-29T11:23): 게이트가 전사 파일의 마지막 어시스턴트 텍스트를 읽었는데,
Stop 시점의 전사에는 최종 응답이 아직 없다(실측: 전사 4줄, 최종 응답 부재).
그래서 직전 턴의 산문을 판정해 머리 표기가 **있는** 응답을 차단했다 —
오차단은 시정 경로를 봉쇄하는 최악형이다(S§9.3-14).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))

from harness_core import clock, hooks as H  # noqa: E402

ROLES = {"lead", "pm", "qa"}
NAMES = {"타치코마", "쿠제"}
HEAD = "[2026-07-29T11:23:44+09:00 · 타치코마 · lead]"


def test_stop_input_prefers_last_assistant_message():
    """실측: Stop 훅 입력에 `last_assistant_message`(문자열)가 있고 그것이
    최종 응답 전문이다. 전사 파일은 그 시점에 최종 응답을 담지 않는다."""
    payload = {"last_assistant_message": HEAD + "\n\n본문",
               "transcript_path": "/does/not/exist.jsonl"}
    assert H.response_text(payload).startswith("[2026-07-29")


def test_stop_input_falls_back_to_transcript_when_key_absent(tmp=None):
    import json
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    tp = d / "t.jsonl"
    tp.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": HEAD + "\n전사판"}]},
    }) + "\n", encoding="utf-8")
    assert "전사판" in H.response_text({"transcript_path": str(tp)})


def test_absent_input_yields_empty_not_a_block():
    """입력이 없으면 '위반'이 아니라 '판정 불가'다 — 침묵과 무위반의 구별."""
    assert H.response_text({}) == ""


# ── X8 머리 표기 판정식 ─────────────────────────────────────────
def test_valid_head_passes():
    ok, why = H.check_head(HEAD + "\n\n본문", registry_names=NAMES,
                           role_keys=ROLES, drift_cap_s=900,
                           injected_at=clock.parse_iso("2026-07-29T11:20:00+09:00"))
    assert ok, why


def test_missing_head_blocks():
    ok, why = H.check_head("머리 없이 시작하는 응답", registry_names=NAMES,
                           role_keys=ROLES, drift_cap_s=900, injected_at=None)
    assert not ok and "머리 표기" in why


def test_unregistered_display_name_blocks():
    bad = "[2026-07-29T11:23:44+09:00 · 없는이름 · lead]\n"
    ok, why = H.check_head(bad, registry_names=NAMES, role_keys=ROLES,
                           drift_cap_s=900, injected_at=None)
    assert not ok and "레지스트리" in why


def test_role_key_outside_roster_blocks():
    bad = "[2026-07-29T11:23:44+09:00 · 타치코마 · nosuchrole]\n"
    ok, why = H.check_head(bad, registry_names=NAMES, role_keys=ROLES,
                           drift_cap_s=900, injected_at=None)
    assert not ok and "역할 키" in why


def test_drift_beyond_cap_blocks():
    ok, why = H.check_head(HEAD, registry_names=NAMES, role_keys=ROLES,
                           drift_cap_s=60,
                           injected_at=clock.parse_iso("2026-07-29T10:00:00+09:00"))
    assert not ok and "벌어졌습니다" in why


def test_missing_injected_time_is_not_a_pass_claim():
    """대조 불가는 통과가 아니라 '대조 불가'다 — 그 사실을 사유로 남긴다."""
    ok, why = H.check_head(HEAD, registry_names=NAMES, role_keys=ROLES,
                           drift_cap_s=900, injected_at=None)
    assert ok and "대조 불가" in why


def test_empty_registry_does_not_block_on_name():
    """레지스트리가 비면 이름 축은 판정하지 않는다 — 설치 전 구간이 정상 존재한다."""
    ok, _ = H.check_head(HEAD, registry_names=set(), role_keys=ROLES,
                         drift_cap_s=900, injected_at=None)
    assert ok


# ── F§7.9 상찬 어휘 (닫힌 어휘 × 닫힌 대상 필드) ────────────────
def test_praise_in_claim_statement_is_detected():
    block = {"claims": [{"statement": "훌륭한 판정입니다"}]}
    assert H.scan_praise(block, ["훌륭"]) == "훌륭"


def test_praise_outside_closed_fields_is_not_detected():
    block = {"claims": [{"statement": "사실 서술"}], "note": "훌륭한 문서"}
    assert H.scan_praise(block, ["훌륭"]) is None


# ── 존댓말 관측 계층 (판정하지 않는다) ──────────────────────────
def test_honorific_observer_counts_but_never_blocks():
    r = H.observe_honorific("설치를 완료했습니다. 검증도 통과했습니다.")
    assert r["korean_sentences"] >= 2 and r["ratio"] == 1.0
    r2 = H.observe_honorific("설치를 완료했다. 검증도 통과했다.")
    assert r2["ratio"] == 0.0
    assert "verdict" not in r2 and "block" not in r2


def test_honorific_observer_excludes_code_fences():
    text = "완료했습니다.\n```\nprint('반말이다')\n```\n"
    r = H.observe_honorific(text)
    assert r["korean_sentences"] == 1


# ── 드리프트 기준점 오류 (실사고 2026-07-29T03:26) ──────────────
def test_long_turn_with_honestly_measured_head_is_not_blocked():
    """실사고: 머리 표기를 **턴 시작 시각**과 대조했더니, 19분짜리 턴에서
    정직하게 잰 시각이 1,160초 벌어져 차단됐다.

    두 값이 같아야 할 이유가 애초에 없는 짝이었다. 응답 대기 무제한이 정책인
    한(S§4.4), 턴 길이를 상한으로 가정하는 검사는 전부 오차단을 만든다 —
    B§3.6 이 고아 판정에서 같은 함정을 이미 기록했다.
    """
    head = "[2026-07-29T12:26:41+09:00 · 타치코마 · lead]\n본문"
    ok, why = H.check_head(
        head, registry_names={"타치코마"}, role_keys={"lead"}, drift_cap_s=900,
        turn_started_at=clock.parse_iso("2026-07-29T12:07:21+09:00"),
        now=clock.parse_iso("2026-07-29T12:26:50+09:00"))
    assert ok, why


def test_stale_head_reused_from_an_earlier_turn_is_blocked():
    """옛 값을 재사용하면 Stop 시각과 벌어진다 — 그것이 잡으려던 축이다."""
    head = "[2026-07-29T11:00:00+09:00 · 타치코마 · lead]\n본문"
    ok, why = H.check_head(
        head, registry_names={"타치코마"}, role_keys={"lead"}, drift_cap_s=900,
        turn_started_at=clock.parse_iso("2026-07-29T12:07:21+09:00"),
        now=clock.parse_iso("2026-07-29T12:26:50+09:00"))
    assert not ok and "벌어졌습니다" in why


def test_head_time_in_the_future_is_blocked():
    """미래 시각은 실측일 수 없다 — 방향을 가리지 않고 절댓값으로 잰다."""
    head = "[2026-07-29T13:00:00+09:00 · 타치코마 · lead]\n본문"
    ok, why = H.check_head(
        head, registry_names={"타치코마"}, role_keys={"lead"}, drift_cap_s=900,
        turn_started_at=clock.parse_iso("2026-07-29T12:07:21+09:00"),
        now=clock.parse_iso("2026-07-29T12:26:50+09:00"))
    assert not ok


def test_reference_point_is_stop_time_not_turn_start():
    """기준점이 무엇인지를 계약으로 고정한다 — 이 값이 바뀌면 판정이 바뀐다."""
    assert H.DRIFT_REFERENCE == "stop-time"


def test_system_plane_probe_is_not_subject_to_the_head_gate():
    """설치 검증 프로브는 **사람 대면 응답이 아니다.** 'ok' 한 줄을 내는 세션에
    머리 표기를 요구하면 프로브가 차단되고, 그 차단이 검증 축 A1 을 깨뜨린다.

    실측 2026-07-29T03:51: 프로브 세션의 turn_end 에 head_ok=false 가 기록됐고
    사유는 '역할 키가 로스터 밖'이었다 — 프로브의 응답을 에이전트 응답으로
    판정한 것이다. 판정 대상이 아닌 것을 판정하면 오차단이 된다.
    """
    assert H.head_gate_applies({"plane": "workflow"}) is True
    assert H.head_gate_applies({"plane": "interactive"}) is True
    assert H.head_gate_applies({"plane": "system"}) is False


def test_system_plane_still_records_the_observation():
    """면제는 판정 면제이지 기록 면제가 아니다 — 침묵과 무위반을 구별한다."""
    assert H.head_gate_applies({"plane": "system"}) is False
