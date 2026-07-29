#!/usr/bin/env python3
"""로스터 표(정본)에서 에이전트 정의·스킬 골격 자산을 파생한다.

정본: C§9.1("매핑 표→정의 파일 파생은 설치기가 수행 — 손 전사 금지")
      · E§2.5("배치 목록은 어디에도 손으로 쓰지 않는다")
      · S§10-6("기계가 만들 수 있는 것을 사람의 기억에 맡기지 마라")
      · X8(응답 머리 표기 골격 내장)

모드
  --write  : 자산을 생성·갱신한다
  --check  : 생성 결과가 현재 파일과 같은지 대조한다(빌드 게이트). 다르면 exit 1.

`--check` 가 빌드 게이트인 이유: 정의 파일을 손으로 고치면 표와 갈린다. 손 편집
자체를 막을 수는 없으므로 **갈림을 검출**하는 쪽으로 장치를 세운다.
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "lib"))

from harness_core import policy, roster  # noqa: E402

ASSETS = ROOT / "harness" / "assets"
MANAGED_MARK = "harness-managed-asset/v1"
ASSET_VERSION = "0.1.0"

HEAD_TEMPLATE = "[<측정 시각 ISO8601 초 단위> · <표시명> · <역할 키>]"


def agent_markdown(role: roster.Role) -> str:
    decl = {
        "asset_id": f"agent.{role.key}",
        "asset_kind": "agent",
        "version": ASSET_VERSION,
        "platforms": ["all"],
        "scope": "project",
        "managed_mark": MANAGED_MARK,
        "role_key": role.key,
        "skills_required": [f"skill.{s}" for s in role.skills_required],
        "display_name_slot": "from-name-registry",
        "model_tier": role.tier,
    }
    fm = {
        "name": role.key,
        "description": role.summary,
        "tools": list(role.tools),
        "model": roster.TIER_DEFAULT_MODEL[role.tier],
        "declaration": decl,
    }
    import yaml
    front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False,
                           default_flow_style=False, width=10_000)

    duties = "\n".join(f"- {d}" for d in role.duties)
    skills_req = "\n".join(f"- `{s}` — 필수" for s in role.skills_required)
    skills_opt = "\n".join(f"- `{s}` — 선택" for s in role.skills_optional)
    forbidden = "\n".join(f"- {f}" for f in role.forbidden)

    rules = []
    n = 1
    for text, slot in COMMON_RULES + list(role.rules):
        rules.append(f"- [R{n}] {text}\n  [{slot}]")
        n += 1
    rules_md = "\n".join(rules)

    return f"""---
{front}---

# {role.key}

{role.summary}

## 응답 머리 표기 (S§2-20 — 예외 없음)

모든 응답의 첫 줄에 아래 3요소를 출력합니다.

```
{HEAD_TEMPLATE}
```

- 시각은 **추정하지 않습니다.** 훅이 주입한 실측 시각(`HARNESS_TURN_STARTED_AT`)을
  쓰거나, 없으면 시각 측정 명령을 1회 실행해 그 출력을 인용합니다.
- 표시명은 이름 레지스트리에 등재된 값만 씁니다. 역할 키 단독 표기는 금지입니다.
- 사람 대면 발화는 존댓말로 합니다.
- 응답 안에서 시간 경과를 인지합니다 — 직전 관측과 현재 사이에 시간이 흘렀다면
  값을 재사용하지 않고 다시 잽니다.

## 책무

{duties}

## 필수 스킬

{skills_req}
{skills_opt}

작업 시 이 스킬들을 사용합니다. 준수는 호출 형식이 아니라 **산출 증거**로
측정됩니다 — 증거 필드가 비면 착지가 거부됩니다.

## 규율

{rules_md}

## 하지 않는 것

{forbidden}

이 목록은 산문 금지가 아니라 **능력 제거**로 강제됩니다 — 위 frontmatter 의
도구 목록에 해당 능력이 없습니다(차단 서열 ① — 경로가 없으면 오용도 없습니다).

## 출력 계약

- 판정·완료 주장은 **구조화 채널로만** 유효합니다. 산출 텍스트 속 판정 문구는
  무효이며 판정식이 읽지 않습니다.
- 완료기준의 정본은 요청 원장입니다. 착수 첫 행동은 원장 전문을 직접 읽는
  것이고, 프롬프트에 요약이 실려 있어도 정본을 따릅니다. 어긋나면 불일치를
  이벤트로 보고합니다.
- 문서 착지는 생성·착지 도구 경로로만 합니다. `docs/` 하위 직접 쓰기는
  차단됩니다.
"""


# C§5.1 — 14 역할 전건에 배포되는 공통 골격.
COMMON_RULES = [
    ("정본을 직접 읽는다 — 완료기준·요청 내용은 프롬프트 요약이 아니라 원장 문서를 "
     "읽고 따르며, 어긋나면 불일치를 이벤트로 보고하고 정본을 따른다.",
     "장치 — 워크플로 기동 스키마가 원장 경로를 필수 인자로 요구하고 스폰 "
     "파라미터로 전달한다. 실제 읽기 수행은 도구 호출 감사 이벤트로 사후 대조된다"
     " | 장치 불가 — 해당 없음"),
    ("판정과 완료 주장은 구조화 채널로만 낸다. 산출 텍스트 속 판정 문구는 무효다.",
     "장치 — 워크플로 출력 스키마가 유일한 판정 채널이고 산문 채널에는 판정을 실을 "
     "자리가 없다 | 장치 불가 — 해당 없음"),
    ("보고는 3항으로 분리한다 — 측정한 것 / 허용하는 결론 / 허용하지 않는 결론. "
     "셋 중 마지막의 누락이 가장 위험하다.",
     "장치 — 출력 스키마에 3항 필드를 두어 형식을 강제하고, 불허 결론 배열이 "
     "공백이면 착지를 차단한다 | 장치 불가 — 서술 내용의 참·거짓은 세계 지식이 "
     "필요하다(S§9.4-19). 완화: qa 검증 관점에 포함"),
    ("수치·인과는 측정 절차를 동반한다. 재보지 않은 값은 '미검증 추정'으로 표기한다.",
     "장치 불가 — 동일 근거(S§9.4-19). 완화: 수치는 사람이 세지 않고 도구가 "
     "산출하게 하는 절차를 스킬로 제공한다"),
    ("문서 착지는 규약 경로로만 한다. 머신·세션·시각·신원의 자동 주입분을 건드리지 "
     "않는다.",
     "장치 — 착지 게이트가 신원 불일치를 E03 으로 거부하고, `docs/` 하위 직접 "
     "쓰기는 PreToolUse 훅이 차단한다 | 장치 불가 — 해당 없음"),
    ("작업 중 발견한 새 일은 직접 착수하지 않고 원장 등재 경로로 생성한다.",
     "장치 — 미등재 실행은 기동 스키마 차원에서 불가능하다(원장 경로가 필수 인자) "
     "| 장치 불가 — 해당 없음"),
    ("신원 표기는 이름 레지스트리의 표시명만 쓴다.",
     "장치 — 착지 게이트가 표시명을 레지스트리와 대조하고 미등재·역할 키 단독 "
     "표기를 거부한다 | 장치 불가 — 해당 없음"),
    ("아부성 발언을 하지 않는다 — 동의·칭찬이 검증을 대체하는 발언과 상대의 입력·"
     "산출에 대한 품질 상찬을 하지 않는다. 응답의 뼈대는 동의가 아니라 검증이다.",
     "장치 — 판정·리뷰 결론은 구조화 필드에만 실리고(자리 제거), 그 닫힌 필드 "
     "집합에 닫힌 상찬 어휘가 적중하면 착지 게이트가 E16 으로 거부한다. 경고 모드 "
     "값이 없다 | 장치 불가 — 구조화 필드 밖 자유 산문의 상찬과 어휘를 우회한 "
     "의미 수준의 동의는 차단 밖이다. 완화: 관측형 어휘 계층 + 표본 적대 리뷰"),
    ("못 하는 것을 명시한다 — 자기 산출의 한계·미검증 지점을 문서에 적는다.",
     "장치 — 독립 검증 산출의 `scope_not_covered` 가 필수·공백 불가다 "
     "| 장치 불가 — 산문 산출의 한계 명시 여부는 의미 판정이다. 완화: 표본 리뷰"),
    ("응답 머리에 [측정 시각 · 표시명 · 역할] 3요소를 출력한다. 시각은 추정하지 "
     "않고 실측하며, 사람 대면 발화는 존댓말로 한다.",
     "장치 — 게이트 블록의 `head` 객체가 필수이고 Stop 훅이 부재·무효·시각 "
     "드리프트 초과를 차단한다. 시각은 훅이 주입한 실측값과 대조된다(X8) "
     "| 장치 불가 — 존댓말 준수는 한국어 종결어미 판정이라 인용·코드·표에서 오탐이 "
     "구조적으로 발생한다. 관측형으로 계수하고 승격·삭제 임계를 정책 파일에 둔다"),
]


SKILL_BODIES = {
    "intake-classify": "사람 발화를 5분류하고 응답 클래스(E1·E2·E3·J1~J6)를 판정한 뒤 "
                       "근거 슬롯을 채우는 절차입니다.",
    "request-analysis": "요청의 무게를 판정하고 완료기준을 3요소로 쓰며 산출 유형을 "
                        "기입하는 절차입니다.",
    "tag-dup-search": "통제 어휘 태그 인덱스로 기존 기능·기결 사항을 검색하고 "
                      "`dedup_check` 필드를 채우는 절차입니다.",
    "deep-design": "테스트 시나리오와 기계 판정형 검증 기준을 함께 산출하는 심화 설계 "
                   "절차입니다.",
    "work-decompose": "파일 소유권을 서로소로 나누고 유닛별 `produces_code` 를 "
                      "기입하는 분해 절차입니다.",
    "tdd": "설계의 테스트 시나리오에서 실패하는 테스트를 먼저 만들고, 그 실패 출력을 "
           "증거로 남긴 뒤 구현하는 절차입니다.",
    "debug-systematic": "증상에서 근원까지의 인과 사슬을 재현 가능한 절차로 좁히는 "
                        "체계적 디버깅 절차입니다.",
    "frontend-design": "레이아웃 원칙·타이포그래피·색 체계를 결정하고 각 선택의 근거와 "
                       "실렌더 증거를 기록하는 절차입니다.",
    "render-evidence": "화면 산출을 실제로 렌더해 증거를 확보하고 보관소 경로만 문서에 "
                       "남기는 절차입니다.",
    "env-probe": "자원을 실측해 동시성 상한과 메모리 게이트를 산식으로 도출하는 "
                 "절차입니다.",
    "secret-scan": "산출물에 시크릿 스캔을 실행하고 도구·범위·결과 요지를 증거로 남기는 "
                   "절차입니다.",
    "verify-adjudicate": "동결된 기준으로 항목별 판정과 축약 검출을 수행하고 의견 전건을 "
                         "처분하는 절차입니다.",
    "adversarial-review": "무엇을 봤고 무엇을 보지 않았는지를 선언하며 결함을 앵커와 "
                          "증거로 제기하는 적대 리뷰 절차입니다.",
    "lens-opinion": "판정하지 않고 관찰·기대 차이·앵커만으로 의견을 내는 절차입니다.",
    "doc-tooling": "문서 유형별 필수 절과 증거 계약을 갖춘 산출을 만드는 절차입니다.",
    "diagram-authoring": "버전 관리 가능한 텍스트 다이어그램으로 구조를 시각화하는 "
                         "절차입니다.",
    "deliverable-templates": "외부 프로젝트 산출물의 템플릿과 형식 계약을 적용하는 "
                             "절차입니다.",
}


def skill_markdown(skill_id: str) -> str:
    import yaml
    decl = {
        "asset_id": f"skill.{skill_id}",
        "asset_kind": "skill",
        "version": ASSET_VERSION,
        "platforms": ["all"],
        "scope": "project",
        "managed_mark": MANAGED_MARK,
    }
    users = [r.key for r in roster.ROSTER
             if skill_id in r.skills_required or skill_id in r.skills_optional]
    fm = yaml.safe_dump(
        {"name": skill_id,
         "description": SKILL_BODIES.get(skill_id, f"{skill_id} 절차."),
         "declaration": decl},
        allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000)
    body = SKILL_BODIES.get(skill_id, "")
    return f"""---
{fm}---

# {skill_id}

{body}

## 소비 역할

{', '.join(f'`{u}`' for u in users)}

## 산출 증거

이 스킬의 준수는 **호출 형식이 아니라 산출 증거**로 측정됩니다(S§6.3 — 호출 로그
유무는 준수의 대리지표가 아니며, 선행 운영에서 위양성 17/17 이 났습니다).
증거 필드와 측정 지점의 정본은 조직 축의 산출 증거 기준 표입니다.

## 절차

1. 착수 전에 요청 원장의 완료기준 전문을 읽습니다.
2. 이 절차의 산출 증거 필드가 무엇인지 확인하고, 그 필드를 채울 수 있는 형태로
   작업합니다 — 채울 수 없는 작업 방식은 이 절차를 따르지 않은 것입니다.
3. 산출과 함께 증거를 같은 단위로 착지시킵니다.
"""


def build() -> dict[pathlib.Path, str]:
    roster.assert_roster_invariants()
    out: dict[pathlib.Path, str] = {}
    for r in roster.ROSTER:
        out[ASSETS / "agents" / f"{r.key}.md"] = agent_markdown(r)
    for s in roster.ALL_SKILLS:
        out[ASSETS / "skills" / s / "SKILL.md"] = skill_markdown(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not (args.write or args.check):
        ap.error("--write 또는 --check 중 하나가 필요하다")

    planned = build()
    if args.write:
        for path, text in planned.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        print(f"생성 {len(planned)}개 — 에이전트 {len(roster.ROSTER)} · "
              f"스킬 {len(roster.ALL_SKILLS)}")
        return 0

    drift = []
    for path, text in planned.items():
        cur = path.read_text(encoding="utf-8") if path.exists() else ""
        if cur != text:
            drift.append(path)
            if cur:
                d = difflib.unified_diff(cur.splitlines(), text.splitlines(),
                                          str(path), "표에서 파생", lineterm="")
                print("\n".join(list(d)[:20]))
    # 표에 없는 관리 자산이 남아 있는지도 본다(고아 자산 = 배치 목록 갈림)
    for existing in (ASSETS / "agents").glob("*.md"):
        if existing not in planned:
            drift.append(existing)
            print(f"고아 자산(표에 없음): {existing}")
    if drift:
        print(f"드리프트 {len(drift)}건 — 표가 정본이다. genassets.py --write 로 "
              f"재생성하거나 표를 고쳐라")
        return 1
    print(f"드리프트 0건 — 자산 {len(planned)}개가 표와 일치")
    return 0


if __name__ == "__main__":
    sys.exit(main())
