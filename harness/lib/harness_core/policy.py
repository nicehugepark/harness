"""정본: S§8 엔진 청결 · F§11.2 정책 파일 · B§9 제안값 총괄표 · E§4.4 산식 파라미터.

값은 코드에 박지 않는다. 사람·조직·경로·시간대 값이 엔진 코드에 박히면
다른 사람의 설치와 초기화 후 자신의 설치가 깨진다(S§8 실사고: 고유 식별자가
실행 코드 33곳에 박혀 유출 가드가 push 를 정당하게 차단했다).

여기 있는 DEFAULTS 는 "코드에 박은 값"이 아니라 **정책 파일 부재 시의 부트
기본값**이며, 설치기가 첫 실행에서 파일로 외재화한다. 전부 미검증 제안값이고
그 지위를 값 옆에 표기한다.
"""
from __future__ import annotations

import json
import pathlib

POLICY_REL = "config/local/policy.json"
LEAD_GATE_POLICY_REL = "config/local/lead-gate-policy.json"

# ── 미검증 제안값 (B§9 · F§11.2 · X8) ───────────────────────────
DEFAULTS = {
    # 파이프라인 루프 상한 — B§9, 전부 미검증 제안
    "K_rework": 3,
    "E_escalation": 1,
    "Ed_design_review": 2,
    "R_restart": 2,
    "Rm_merge_retry": 6,
    "merge_backoff_base_s": 5,
    # 스케줄러 — B§9
    "tick_interval_min": 15,
    "heartbeat_interval_min": 10,
    "liveness_check_window_min": 30,
    "aging_step_hours": {"3": 4, "2": 12, "1": 24},
    "starvation_hours": 48,
    "agent_priority_cap": 2,
    "harness_self_wip_quota_ratio": 0.30,
    "derive_depth_cap": 2,
    "rollup_weekly": True,
    "rollup_failure_trigger": 5,
    "proposal_threshold": 3,
    "hold_renotify_days": 7,
    # 착지·커밋 배치 — A§5.3 (config/local 정책값, 기본 T=60·N=20)
    "batch_commit_idle_s": 60,
    "batch_commit_count": 20,
    # 스트림 회전 — A§2.4-5
    "stream_rotate_bytes": 8 * 1024 * 1024,
    "stream_rotate_lines": 10_000,
    "record_max_bytes": 4096,          # D§2.3 R38-2 계약 상수
    "append_mode": "atomic",           # D§2.3 R38-4 — 프리플라이트가 결정
    # 통지 백오프 — D§6.2
    "escalation_backoff_min": [30, 120, 480, 1440],
    # 리드 게이트 — F§11.2
    "retry_ceiling": 2,
    "numeric_tolerance": 0,
    "review_cap_per_rollup": 10,
    "modal_scan": "observe",
    "modal_promote_min_docs": 30,
    "modal_promote_max_fp": 10,
    "modal_delete_min_fp": 50,
    "new_device_min_observations": 3,
    "retention_days": 365,
    # X4 독립 검증
    "required_reviewers": 1,
    # X8 응답 머리 표기
    "head_time_drift_s": 900,
    "honorific_scan": "observe",
    "honorific_promote_min_responses": 30,
    "honorific_promote_max_fp": 10,
    "honorific_delete_min_fp": 50,
    # 경로 프로파일
    "backup_root": "~/harness-backups",
    "worktree_base": "~/harness-worktrees",
    "branch_prefix": "req",
    "integration_ref": "main",
    # 백업 보존 — E§2.3
    "snapshot_keep": 3,
}

# F§7.9 — 상찬 어휘 닫힌 목록. 코드 하드코딩 금지 원칙상 이것도 파일로 나간다.
# 어간 매칭이 아니라 부분 문자열 대조이며, 대상 필드가 닫혀 있어 결정론이다.
PRAISE_LEXICON = [
    "훌륭", "탁월", "완벽", "멋진", "멋지", "대단",
    "좋은 지적", "좋은 질문", "명료합니다", "정확한 지적",
    "인상적", "감사합니다만", "말씀하신 대로 훌륭",
]


def load(root_dir) -> dict:
    """정책 파일을 읽어 DEFAULTS 위에 덮는다. 파일 부재는 결함이 아니다 —
    설치 전·부트스트랩 구간이 정상적으로 존재하기 때문이다."""
    root = pathlib.Path(root_dir)
    merged = dict(DEFAULTS)
    merged["praise_lexicon"] = list(PRAISE_LEXICON)
    for rel in (POLICY_REL, LEAD_GATE_POLICY_REL):
        p = root / rel
        if p.exists():
            try:
                merged.update(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                # 파싱 실패를 조용히 통과시키지 않는다 — 호출부가 판정하도록
                # 표식을 남긴다(침묵과 무위반의 구별 — S§7).
                merged.setdefault("_policy_load_errors", []).append(rel)
    return merged


def write_defaults(root_dir) -> list[pathlib.Path]:
    """설치기가 첫 실행에서 값을 파일로 외재화한다."""
    root = pathlib.Path(root_dir)
    out = []
    lead_keys = {
        "retry_ceiling", "numeric_tolerance", "review_cap_per_rollup",
        "modal_scan", "modal_promote_min_docs", "modal_promote_max_fp",
        "modal_delete_min_fp", "new_device_min_observations", "retention_days",
        "head_time_drift_s", "honorific_scan", "honorific_promote_min_responses",
        "honorific_promote_max_fp", "honorific_delete_min_fp",
    }
    lead = {k: DEFAULTS[k] for k in sorted(lead_keys)}
    lead["praise_lexicon"] = list(PRAISE_LEXICON)
    lead["praise_target_fields"] = [
        "criteria[].what", "criteria[].pass",
        "summary.verdict_sentence",
        "opinion.observation", "opinion.expectation_gap",
        "review.findings[].claim",
    ]
    main = {k: v for k, v in DEFAULTS.items() if k not in lead_keys}
    main["_status"] = "전건 미검증 제안값 — 운용 실측으로 보정한다(B§9·PB-B25)"
    for rel, data in ((POLICY_REL, main), (LEAD_GATE_POLICY_REL, lead)):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        out.append(p)
    return out
