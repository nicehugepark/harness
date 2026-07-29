---
name: deep-design
description: 테스트 시나리오와 기계 판정형 검증 기준을 함께 산출하는 심화 설계 절차입니다.
declaration:
  asset_id: skill.deep-design
  asset_kind: skill
  version: 0.1.0
  platforms:
  - all
  scope: project
  managed_mark: harness-managed-asset/v1
---

# deep-design

테스트 시나리오와 기계 판정형 검증 기준을 함께 산출하는 심화 설계 절차입니다.

## 소비 역할

`architect`, `staff-engineer`

## 산출 증거

이 스킬의 준수는 **호출 형식이 아니라 산출 증거**로 측정됩니다(S§6.3 — 호출 로그
유무는 준수의 대리지표가 아니며, 선행 운영에서 위양성 17/17 이 났습니다).
증거 필드와 측정 지점의 정본은 조직 축의 산출 증거 기준 표입니다.

## 절차

1. 착수 전에 요청 원장의 완료기준 전문을 읽습니다.
2. 이 절차의 산출 증거 필드가 무엇인지 확인하고, 그 필드를 채울 수 있는 형태로
   작업합니다 — 채울 수 없는 작업 방식은 이 절차를 따르지 않은 것입니다.
3. 산출과 함께 증거를 같은 단위로 착지시킵니다.
