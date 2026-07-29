# 하네스 — 다수 AI 에이전트 운영 체계

Claude Code 위에서 다수의 AI 에이전트가 사람의 요청을 **분석 → 설계 → 구현 → 검증**까지
수행하도록 조직하는 운영 체계입니다. 문서 체계·실행 파이프라인·에이전트 조직·설치기가
한 몸입니다.

**이 디렉토리가 실행 루트이자 저장소 루트입니다.** `docs/` 는 그 직계 자식입니다.

## 무엇부터 보나

| 목적 | 여는 곳 |
|---|---|
| 설계 전체를 통독한다 | `docs/design/public/2026/07/` 의 `…-harness-design-entry-point.md` |
| 임의 계약의 정본 주소를 찾는다 | 같은 진입점 문서의 계약 색인 절 |
| 요구사항 정본을 본다 | `docs/reference/public/2026/07/` 의 `…-harness-design-prompt-shared.md` |
| 설계 시정 내역을 본다 | `docs/design/public/2026/07/` 의 `…-harness-design-correction-batch-8.md` |
| 설치·재설치·제거를 한다 | `harness/installer/install.py` |

설계 문서는 **진입점 1본 → 축 문서 1본** 2홉 안에 임의 주제의 정본에 도달하도록
구성돼 있습니다. 전문 검색으로 정본을 찾는 것은 미도달로 칩니다.

## 트리

```
docs/                 문서 체계. 카테고리 → 가시성 → 연 → 월 고정 경로
  requests/           요청 원장
  design/             설계·분석·개발·검증 단계 산출
  reference/          참조(요구사항 스펙·통제 어휘)
  audit/              감사 스트림 (JSONL, 기계 소비)
  failures/           실패 사슬 문서 + 사건 스트림
  decisions/          내려진 결정
  adjudications/      의사결정 요청 (사람 응답 대기)
  minutes/ handoff/   회의록 · 인계
harness/              하네스 자산 원본 — 배치의 유일한 소스
  assets/             에이전트 14 · 스킬 17 · 훅 5 · 설정 단편 2
  installer/          설치 트랜잭션 + 플랫폼 프로파일 + 자원 산식
  lib/harness_core/   공용 구현(채번·경로·게이트·봉투·정책·레지스트리)
  tools/              착지 도구 · 자산 생성기 · 계측기 · 릴리스 게이트
  tests/              계약 시험
config/               로컬 정책·상태 (버전관리 밖 · 백업 필수)
vault/                민감정보 볼트 (버전관리 밖)
derived/              파생물 전량 (버전관리 밖 · 재생성 가능)
```

`config/`·`vault/`·`derived/`·`docs/*/private/`·`.claude/` 는 버전관리 밖입니다.
보존은 백업 장치가 책임집니다.

## 설치

```
python3 harness/installer/install.py status                 # 현재 상태
python3 harness/installer/install.py install                # 설치·재설치(멱등)
python3 harness/installer/install.py uninstall              # 매니페스트 기준 제거
python3 harness/installer/install.py rollback --snapshot <경로>
```

설치는 `준비 → 인터뷰 → 백업 → 초기화 → 배치 → 검증 → 커밋` 단일 트랜잭션입니다.
검증에 실패하면 자동 롤백합니다. 백업 없는 초기화 경로는 존재하지 않습니다.
배치 목록은 어디에도 손으로 적혀 있지 않고 `harness/assets/**` 글롭 열거 +
자산 선언 파싱으로만 만들어집니다.

## 검사

```
python3 harness/tests/run.py            # 계약 시험 전건
python3 harness/tools/iv1.py            # 릴리스 게이트(청정 루트 실설치·멱등·롤백·언인스톨)
python3 harness/tools/genassets.py --check   # 자산이 로스터 표와 일치하는지
python3 harness/tools/measure.py        # 설치 전 실측 항목 계측
```

종료 코드는 `0 = 정상` · `1 = 검출·불통과` · `2 = 실행 오류`입니다.
**2를 0으로 읽지 않습니다** — 오류와 검출 0건을 구별하지 못한 실행이 검사 한 종을
통째로 무효화한 이력이 있습니다.

## 문서를 쓰는 방법

`docs/` 하위 직접 쓰기는 훅이 차단합니다. 착지는 생성 도구를 경유합니다.

```
python3 harness/tools/land.py --type DS --title "제목" --visibility public \
    --what "무슨 일" --why "왜" --tags domain/harness-self --stage design \
    --body-file 본문.md
```

`--visibility` 에 기본값은 없습니다. 미지정은 존재하지 않는 값이라 착지가 거부됩니다.
본문은 파일 또는 표준입력으로만 받습니다 — 셸이 본문을 해석해 내용이 잘려나가는
경로를 두지 않기 위해서입니다.

## 응답 규율

모든 에이전트는 응답 머리에 `[측정 시각 · 표시명 · 역할]` 3요소를 출력합니다.
시각은 추정하지 않고 실측하며, 훅이 주입한 값과 대조됩니다. 사람 대면 발화는
존댓말로 합니다. 판정·리뷰 결론은 구조화 필드에만 실리고, 그 닫힌 필드 집합에
상찬 어휘가 적중하면 착지가 거부됩니다.
