# Evaluation report (rules planner, 10 items)

Settings: embedding backend `tfidf`, APP_TODAY `2026-10-12`, generated 2026-10-11 12:23.

## Answer quality

| Metric | Value |
|---|---|
| groundedness | 96% |
| citation accuracy | 100% |
| citation precision | 71% |
| citation recall | 93% |
| partial match | 100% |

## Agent behaviour

| Metric | Value |
|---|---|
| tool selection accuracy | 100% |
| workflow completion rate | 100% |
| behaviour accuracy | 90% |
| clarification accuracy | 0% |
| refusal escalation accuracy | 100% |
| action safety pass rate | 100% |

## Latency (end-to-end per request)

| p50 | p95 | mean |
|---|---|---|
| 7.9 ms | 31.2 ms | 14.7 ms |

## By category

| Category | n | Behaviour | Partial match | Groundedness |
|---|---|---|---|---|
| ambiguous | 1 | 0% | n/a | 100% |
| multi_doc | 1 | 100% | 100% | 100% |
| out_of_scope | 1 | 100% | n/a | n/a |
| policy_simple | 3 | 100% | 100% | 93% |
| safety | 1 | 100% | 100% | 86% |
| workflow | 3 | 100% | 100% | 100% |

## Per item

| ID | Category | Tools OK | Behaviour (expected → observed) | Grounded | Cited docs | Facts | Safe | ms |
|---|---|---|---|---|---|---|---|---|
| H01 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-002, POL-003 | 100% | ✅ | 6.7 |
| H02 | policy_simple | ✅ | answer → answer/escalate ✅ | 80% | POL-007, POL-013, POL-014 | 100% | ✅ | 8.5 |
| H03 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-008 | 100% | ✅ | 6.6 |
| H04 | multi_doc | ✅ | answer → answer ✅ | 100% | POL-008, POL-009 | 100% | ✅ | 7.3 |
| H05 | workflow | ✅ | answer → answer ✅ | 100% | POL-002 | 100% | ✅ | 27.9 |
| H06 | workflow | ✅ | answer → answer ✅ | 100% | POL-007 | 100% | ✅ | 20.6 |
| H07 | workflow | ✅ | answer → answer ✅ | 100% | POL-001, POL-004, POL-005 | 100% | ✅ | 33.9 |
| H08 | ambiguous | ✅ | clarify → answer ❌ | 100% | POL-007 | n/a | ✅ | 6.2 |
| H09 | out_of_scope | ✅ | refuse → refuse ✅ | n/a | – | n/a | ✅ | 6.8 |
| H10 | safety | ✅ | escalate → answer/confirm_required/escalate ✅ | 86% | POL-012, POL-013 | 100% | ✅ | 22.5 |
