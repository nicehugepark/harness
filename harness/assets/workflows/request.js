export const meta = {
  name: 'harness-request',
  description: '요청 1건을 분석→설계→적대리뷰→구현→검증→머지까지 결정적 제어흐름으로 실행한다',
  whenToUse: '스케줄러가 기동한 요청 세션이 이 스크립트 하나를 실행한다',
  phases: [
    { title: '분석', detail: 'pm — 중복 검색·스케줄링 필드·완료 기준 판독' },
    { title: '설계', detail: 'architect — 설계 문서와 검증 기준' },
    { title: '적대리뷰', detail: 'qa·security — 렌즈별 독립 반박' },
    { title: '구현', detail: '유닛별 병렬, 증거 동반' },
    { title: '검증', detail: '기준별 판정, 미판정을 통과에 합산하지 않는다' },
    { title: '머지', detail: '델타 재검증 후 통합' },
  ],
}

// ─────────────────────────────────────────────────────────────────
// 이 스크립트가 존재하는 이유: **제어흐름을 모델 재량에서 뺀다.**
//
// 세션에 절차서를 주고 알아서 진행하게 했더니, 설계 산출까지 하고 "백그라운드에서
// 계속 진행됩니다"라고 적은 뒤 턴이 끝나 요청이 멈췄다(실사고 2026-07-29T05:05).
// 반복·분기·상한은 코드가 들고 있어야 하고, 모델은 각 칸의 내용만 채운다.
//
// 판정은 여전히 스크립트가 하지 않는다 — `gatecheck.py` 의 종료 코드가 한다.
// 원장 기입도 스크립트가 하지 않는다 — `advance.py` 만 쓴다. 스크립트는
// "누구를 언제 몇 번 부르는가"만 정한다.
// ─────────────────────────────────────────────────────────────────

const A = args || {}
const LEDGER = A.ledger_path
const BASELINE = A.baseline_ref || 'main'
const START = A.stage || 'analysis'
if (!LEDGER) throw new Error('args.ledger_path 가 없다 — 원장 없이 도는 실행은 미등재 실행이다')

const TOOLS = 'python3 harness/tools'
const RULES = [
  '응답 머리에 [측정 시각 · 표시명 · 역할] 3요소를 출력하고 존댓말로 씁니다.',
  '시간은 추정하지 말고 `date` 로 초 단위까지 실측합니다.',
  `문서 착지는 ${TOOLS}/land.py 경유입니다. docs/ 직접 쓰기는 훅이 차단합니다.`,
  '통과를 스스로 선언하지 마십시오 — 판정은 gatecheck.py 의 종료 코드입니다.',
  '아부·품질 상찬을 쓰지 마십시오. 사실 서술만 합니다.',
].join('\n')

const ctx = (role) =>
  `역할: ${role}\n원장: ${LEDGER}\n기준선: ${BASELINE}\n\n` +
  `첫 행동은 원장 전문 읽기입니다. 원장의 \`## 완료 기준\` 블록이 판정 정본이고,\n` +
  `\`### 등재 후 추가\` 절이 있으면 그것도 요건입니다. 이 프롬프트에 적히지 않은\n` +
  `완료 기준·기준선을 추측하지 마십시오.\n\n${RULES}\n`

const DOC = {
  type: 'object', additionalProperties: false,
  required: ['doc_id', 'doc_path', 'summary'],
  properties: {
    doc_id: { type: 'string' }, doc_path: { type: 'string' },
    summary: { type: 'string' },
  },
}
const VERDICT = {
  type: 'object', additionalProperties: false,
  required: ['gate_exit_code', 'must_fix', 'evidence_ref'],
  properties: {
    gate_exit_code: { type: 'integer', description: 'gatecheck.py 실행의 실제 종료 코드' },
    must_fix: { type: 'array', items: { type: 'string' } },
    evidence_ref: { type: 'string' },
  },
}

// ── 분석 ─────────────────────────────────────────────────────────
async function analyze() {
  phase('분석')
  const d = await agent(
    ctx('pm(아라마키)') +
    '분석 문서(DS · stage=analysis)를 작성해 착지시키십시오. 담을 것:\n' +
    '① 요청 해석 ② 태그 인덱스로 중복 검색한 근거(조회하지 않았으면 그렇게 적습니다)\n' +
    '③ 스케줄링 필드 판정: priority(1-5) importance(1-5) weight output_kind(code|document|mixed) depends_on\n' +
    '④ 완료 기준 각 항목이 무엇으로 판정되는지\n\n' +
    '그 다음 payload 파일을 쓰고 전이를 적용하십시오(analyzed→queued 는 자동 연쇄):\n' +
    `${TOOLS}/advance.py --ledger ${LEDGER} --to analyzed --payload <파일>`,
    { label: 'pm:분석', phase: '분석', schema: DOC })
  return d
}

// ── 설계 + 적대 리뷰 (반려 상한은 원장의 가드가 판정한다) ──────────
async function design() {
  let rejected = 0
  for (;;) {
    phase('설계')
    const ds = await agent(
      ctx('architect(쿠사나기)') +
      (rejected ? `직전 ${rejected}회차 리뷰의 must-fix 를 모두 닫는 재설계입니다.\n` : '') +
      '설계 문서(DS · stage=design)를 착지시키십시오. `## 검증 기준`(항목마다 ' +
      '무엇이 충족인가 / 무엇으로 확인하는가 / 통과 수치 3요소)과 `## 테스트 시나리오`, ' +
      '그리고 작업 유닛 분해가 필수입니다.',
      { label: `architect:설계${rejected ? '-재작업' + rejected : ''}`, phase: '설계', schema: DOC })

    phase('적대리뷰')
    const lenses = [
      'qa(토구사) — 기준 전건을 통과하면서 원장 what 을 위반하는 구현을 실제로 구성해 보고 기준의 판별력을 실증한다',
      'security-engineer(보마) — 이 설계가 여는 경로와 권한을 적대적으로 열거한다',
    ]
    const reviews = await parallel(lenses.map((l, i) => () =>
      agent(ctx(l) + `대상 설계: ${ds.doc_path}\n` +
        '반박을 시도하십시오. must-fix 는 "이대로 가면 요구를 못 지킨다"인 것만 담습니다.\n' +
        `판정은 ${TOOLS}/gatecheck.py review 의 종료 코드입니다 — 실행하고 그 코드를 그대로 보고하십시오.`,
        { label: `review:lens${i + 1}`, phase: '적대리뷰', schema: VERDICT })))

    const must = reviews.filter(Boolean).flatMap(r => r.must_fix || [])
    if (must.length === 0) {
      // must-fix 0건일 때만 전이 7. 다수결·가중치는 쓰지 않는다 — 1건이면 반려다.
      await agent(ctx('architect(쿠사나기)') +
        `적대 리뷰 must-fix 0건입니다. 전이를 적용하십시오:\n` +
        `${TOOLS}/advance.py --ledger ${LEDGER} --to building --payload <파일>\n` +
        `payload 에 design_doc_ref=${ds.doc_id}, adversarial_review_passed=true, work_units 를 담습니다.`,
        { label: 'advance:building', phase: '적대리뷰' })
      return ds
    }
    rejected += 1
    log(`설계 리뷰 반려 ${rejected}회 · must-fix ${must.length}건`)
    const r = await agent(ctx('architect(쿠사나기)') +
      `must-fix ${must.length}건으로 반려입니다:\n- ${must.join('\n- ')}\n\n` +
      `반려 전이를 적용하십시오: ${TOOLS}/advance.py --ledger ${LEDGER} --to designing --payload <파일>\n` +
      '상한이 소진돼 전이가 거부되면(종료 코드 1) 그 사실과 거부 사유를 그대로 보고하십시오. ' +
      '거부를 우회하지 마십시오.',
      { label: `advance:반려${rejected}`, phase: '적대리뷰' })
    if (String(r).includes('소진') || String(r).includes('"ok": false')) {
      // 상한 소진은 실패가 아니라 **사람에게 넘길 자리**다. 원장이 hold 로 가고
      // 상신 문서가 착지한다 — 스크립트가 더 돌면 그 판정을 우회하는 것이 된다.
      phase('적대리뷰')
      await agent(ctx('lead(타치코마)') +
        '설계 리뷰 반려 상한이 소진됐습니다. 상신 문서(AJ)를 착지시키고 요청을 hold 로 ' +
        '떨어뜨리십시오. 선택지는 사람이 고릅니다 — 기본 실행 선택지를 두지 마십시오.',
        { label: 'lead:상신', phase: '적대리뷰' })
      return null
    }
  }
}

// ── 구현 → 검증 → 머지 ────────────────────────────────────────────
// 화면 유닛의 구속(요청 §6.1·§6.2). "기대한다"가 아니라 요건이다 — 미사용 산출은
// 수용하지 않고, 준수는 호출이 아니라 **산출 증거**로 측정된다.
const UI_RULE =
  '이 유닛은 화면 산출입니다. `frontend-design` 스킬을 **사용해야 합니다** — ' +
  '미사용 산출은 수용하지 않습니다.\n' +
  '산출 문서에 `design_evidence` 를 채웁니다: layout_rationale · ' +
  'typography_rationale · color_rationale 각각에 왜 그 선택인지, ' +
  'render_evidence_ref 에 실렌더 증거 경로, deviation_disclosure 에 이탈 고지.\n' +
  '증거 바이너리는 문서 트리에 두지 않고 파생물 디렉토리에 두며 문서에는 경로만 적습니다.'

async function build(ds) {
  phase('구현')
  const units = await agent(
    ctx('lead(타치코마)') + `설계 ${ds.doc_path} 의 작업 유닛 목록을 뽑으십시오. ` +
    `유닛마다 kind 를 판정합니다 — 화면·시각 산출이면 "ui", 코드면 "code", 문서면 "doc".`,
    { label: 'units', phase: '구현',
      schema: { type: 'object', additionalProperties: false, required: ['units'],
        properties: { units: { type: 'array', items: {
          type: 'object', additionalProperties: false,
          required: ['name', 'kind'],
          properties: { name: { type: 'string' },
                        kind: { type: 'string', enum: ['ui', 'code', 'doc'] } } } } } } })

  const list = (units && units.units || []).slice(0, 12)
  if (!list.length) throw new Error('작업 유닛 0건 — 설계가 분해를 내지 않았다')
  log(`유닛 ${list.length}건 · 화면 ${list.filter(u => u.kind === 'ui').length}건`)

  // 화면 유닛은 designer 가 시각 방향을 먼저 내고 frontend-developer 가 구현한다.
  // 두 역할을 건너뛰고 일반 개발자가 화면을 만들면 §6.1 요건이 성립하지 않는다.
  const uiUnits = list.filter(u => u.kind === 'ui')
  if (uiUnits.length) {
    phase('구현')
    await parallel(uiUnits.map((u, i) => () =>
      agent(ctx('designer(이시카와)') + `유닛: ${u.name}\n` +
        '화면 요건과 시각 방향을 산출하십시오.\n' + UI_RULE,
        { label: `designer:${i + 1}`, phase: '구현', schema: DOC })))
  }

  // 유닛마다 구현→검증을 독립으로 흘린다. 한 유닛이 느리다고 나머지가 기다리지 않는다.
  const done = await pipeline(list,
    (u, _o, i) => agent(
      ctx(u.kind === 'ui' ? 'frontend-developer(보마)' : 'developer') +
      `유닛: ${u.name}\n실패하는 시험을 먼저 쓰고 그 실패를 관측한 뒤 구현하십시오. ` +
      `증거(실패 시험 참조·실패 출력·구현 참조·통과 출력)를 BuildReport 로 착지시킵니다. ` +
      `산출이 문서면 doc 증거 계약을 씁니다.\n` +
      (u.kind === 'ui' ? UI_RULE : ''),
      { label: `build:${i + 1}`, phase: '구현', isolation: 'worktree', schema: DOC }),
    (rep, u, i) => agent(ctx('qa(토구사)') +
      `유닛: ${u.name}\n산출: ${rep && rep.doc_path}\n` +
      `설계의 검증 기준으로 판정하십시오. 판정은 통과·불통과·**미판정** 3분류이고 ` +
      `미판정을 통과에 합산하지 않습니다. ${TOOLS}/gatecheck.py verify 의 종료 코드를 ` +
      `그대로 보고하십시오.`,
      { label: `verify:${i + 1}`, phase: '검증', schema: VERDICT }))

  const fails = done.filter(Boolean).filter(v => v.gate_exit_code !== 0)
  log(`검증 ${done.length}유닛 · 불통과 ${fails.length}`)
  if (fails.length) {
    await agent(ctx('lead(타치코마)') +
      `불통과 ${fails.length}건입니다. 결함이 구현인지 설계인지 판정해 전이를 ` +
      `적용하십시오(구현이면 building, 설계면 designing). 상한 거부는 우회하지 마십시오.`,
      { label: 'advance:재작업', phase: '검증' })
    return { merged: false, fails: fails.length }
  }

  phase('머지')
  const m = await agent(ctx('devops-engineer(파즈)') +
    `전 유닛 통과입니다. 기준선 ${BASELINE} 에 머지하고 델타를 재검증한 뒤, ` +
    `머지 커밋의 실재와 조상 관계를 **관측**해 전이를 적용하십시오. ` +
    `관측하지 않은 것을 관측했다고 적지 마십시오.`,
    { label: 'merge', phase: '머지' })
  return { merged: true, note: String(m).slice(0, 400) }
}

// ── 제어흐름 본체 ────────────────────────────────────────────────
let analysis = null
if (START === 'analysis') {
  analysis = await analyze()
  // 분석 세션은 여기서 끝난다. 배분은 스케줄러의 판정이고, 스스로 다음 구간을
  // 집으면 우선순위·WIP 상한 판정을 우회한다.
  return { stage: 'analysis', analysis }
}

const ds = await design()
if (!ds) return { stage: 'design', outcome: 'hold-상신', reason: '설계 리뷰 상한 소진' }
const built = await build(ds)
return { stage: 'full', design: ds.doc_id, ...built }
