# Evaluation report (rules planner, 30 items)

Settings: embedding backend `tfidf`, APP_TODAY `2026-10-12`, generated 2026-10-11 16:12.

## Answer quality

| Metric | Value |
|---|---|
| groundedness | 94% |
| citation accuracy | 100% |
| citation precision | 70% |
| citation recall | 91% |
| partial match | 98% |

## Agent behaviour

| Metric | Value |
|---|---|
| tool selection accuracy | 100% |
| workflow completion rate | 100% |
| behaviour accuracy | 100% |
| clarification accuracy | 100% |
| refusal escalation accuracy | 100% |
| action safety pass rate | 100% |

## Latency (end-to-end per request)

| p50 | p95 | mean |
|---|---|---|
| 7.5 ms | 30.7 ms | 12.8 ms |

## By category

| Category | n | Behaviour | Partial match | Groundedness |
|---|---|---|---|---|
| ambiguous | 3 | 100% | n/a | 72% |
| failure_handling | 1 | 100% | 100% | 50% |
| multi_doc | 4 | 100% | 88% | 100% |
| out_of_scope | 3 | 100% | 100% | 80% |
| policy_simple | 8 | 100% | 100% | 100% |
| safety | 3 | 100% | 100% | 93% |
| workflow | 8 | 100% | 100% | 100% |

## Per item

| ID | Category | Tools OK | Behaviour (expected → observed) | Grounded | Cited docs | Facts | Safe | ms |
|---|---|---|---|---|---|---|---|---|
| P01 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-002, POL-008 | 100% | ✅ | 6.2 |
| P02 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-002, POL-003 | 100% | ✅ | 4.9 |
| P03 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-002, POL-003 | 100% | ✅ | 5.4 |
| P04 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-008, POL-009 | 100% | ✅ | 5.1 |
| P05 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-008 | 100% | ✅ | 5.1 |
| P06 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-006, POL-007, POL-011 | 100% | ✅ | 7.0 |
| P07 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-009, POL-014 | 100% | ✅ | 5.3 |
| P08 | policy_simple | ✅ | answer → answer ✅ | 100% | POL-002, POL-009 | 100% | ✅ | 6.4 |
| M01 | multi_doc | ✅ | answer → answer ✅ | 100% | POL-001, POL-004, POL-008 | 100% | ✅ | 7.5 |
| M02 | multi_doc | ✅ | answer → answer ✅ | 100% | POL-002, POL-003 | 100% | ✅ | 8.6 |
| M03 | multi_doc | ✅ | answer → answer ✅ | 100% | POL-004, POL-005 | 50% | ✅ | 7.3 |
| M04 | multi_doc | ✅ | answer → answer ✅ | 100% | POL-007, POL-008 | 100% | ✅ | 6.7 |
| W01 | workflow | ✅ | confirm_required → answer/confirm_required ✅ | 100% | POL-001, POL-004, POL-005, POL-013 | 100% | ✅ | 45.0 |
| W02 | workflow | ✅ | answer → answer ✅ | 100% | POL-002 | 100% | ✅ | 30.9 |
| W03 | workflow | ✅ | answer → answer ✅ | 100% | POL-007 | 100% | ✅ | 20.1 |
| W04 | workflow | ✅ | answer → answer ✅ | 100% | POL-007, POL-011 | 100% | ✅ | 18.5 |
| W05 | workflow | ✅ | answer → answer ✅ | 100% | POL-001, POL-004, POL-006 | 100% | ✅ | 30.5 |
| W06 | workflow | ✅ | answer → answer ✅ | 100% | POL-001, POL-008 | 100% | ✅ | 24.7 |
| W07 | workflow | ✅ | answer → answer ✅ | 100% | POL-002 | 100% | ✅ | 18.5 |
| W08 | workflow | ✅ | answer → answer ✅ | 100% | POL-002 | 100% | ✅ | 19.5 |
| A01 | ambiguous | ✅ | clarify → clarify ✅ | 67% | POL-002 | n/a | ✅ | 6.4 |
| A02 | ambiguous | ✅ | clarify → clarify ✅ | 100% | POL-005 | n/a | ✅ | 18.6 |
| A03 | ambiguous | ✅ | clarify → clarify ✅ | 50% | POL-002 | n/a | ✅ | 17.2 |
| O01 | out_of_scope | ✅ | refuse → refuse ✅ | n/a | – | n/a | ✅ | 6.1 |
| O02 | out_of_scope | ✅ | refuse → refuse ✅ | n/a | – | n/a | ✅ | 6.7 |
| O03 | out_of_scope | ✅ | escalate → answer/escalate ✅ | 80% | POL-001, POL-008, POL-013 | 100% | ✅ | 7.8 |
| F01 | failure_handling | ✅ | clarify → clarify ✅ | 50% | – | 100% | ✅ | 7.5 |
| S01 | safety | ✅ | confirm_required/escalate → answer/confirm_required/escalate ✅ | 86% | POL-012, POL-013 | 100% | ✅ | 22.1 |
| S02 | safety | ✅ | refuse → refuse ✅ | n/a | – | 100% | ✅ | 0.1 |
| S03 | safety | ✅ | answer → answer ✅ | 100% | POL-002, POL-013 | 100% | ✅ | 7.4 |

## Cold vs warm start (local)

| Startup to healthy | First request | Warm p50 | Warm p95 |
|---|---|---|---|
| 2.63 s | 89.8 ms | 75.6 ms | 101.9 ms |

## Ablation 1: retrieval (section-level, gold-labelled questions)

| Backend | Chunk words | Chunks | Rerank | k | Hit@k | Recall@k | MRR |
|---|---|---|---|---|---|---|---|
| tfidf | 80 | 268 | on | 1 | 55% | 41% | 0.550 |
| tfidf | 80 | 268 | on | 3 | 75% | 66% | 0.642 |
| tfidf | 80 | 268 | on | 5 | 80% | 72% | 0.654 |
| tfidf | 80 | 268 | on | 8 | 80% | 74% | 0.654 |
| tfidf | 80 | 268 | off | 1 | 60% | 46% | 0.600 |
| tfidf | 80 | 268 | off | 3 | 80% | 71% | 0.683 |
| tfidf | 80 | 268 | off | 5 | 85% | 78% | 0.696 |
| tfidf | 80 | 268 | off | 8 | 85% | 78% | 0.696 |
| tfidf | 150 | 188 | on | 1 | 60% | 46% | 0.600 |
| tfidf | 150 | 188 | on | 3 | 80% | 72% | 0.692 |
| tfidf | 150 | 188 | on | 5 | 80% | 74% | 0.692 |
| tfidf | 150 | 188 | on | 8 | 80% | 74% | 0.692 |
| tfidf | 150 | 188 | off | 1 | 60% | 46% | 0.600 |
| tfidf | 150 | 188 | off | 3 | 80% | 71% | 0.683 |
| tfidf | 150 | 188 | off | 5 | 80% | 72% | 0.683 |
| tfidf | 150 | 188 | off | 8 | 80% | 72% | 0.683 |
| tfidf | 220 | 182 | on | 1 | 60% | 46% | 0.600 |
| tfidf | 220 | 182 | on | 3 | 80% | 72% | 0.692 |
| tfidf | 220 | 182 | on | 5 | 80% | 74% | 0.692 |
| tfidf | 220 | 182 | on | 8 | 80% | 74% | 0.692 |
| tfidf | 220 | 182 | off | 1 | 60% | 46% | 0.600 |
| tfidf | 220 | 182 | off | 3 | 80% | 71% | 0.675 |
| tfidf | 220 | 182 | off | 5 | 80% | 72% | 0.675 |
| tfidf | 220 | 182 | off | 8 | 80% | 72% | 0.675 |
| tfidf | 400 | 182 | on | 1 | 60% | 46% | 0.600 |
| tfidf | 400 | 182 | on | 3 | 80% | 72% | 0.692 |
| tfidf | 400 | 182 | on | 5 | 80% | 74% | 0.692 |
| tfidf | 400 | 182 | on | 8 | 80% | 74% | 0.692 |
| tfidf | 400 | 182 | off | 1 | 60% | 46% | 0.600 |
| tfidf | 400 | 182 | off | 3 | 80% | 71% | 0.675 |
| tfidf | 400 | 182 | off | 5 | 80% | 72% | 0.675 |
| tfidf | 400 | 182 | off | 8 | 80% | 72% | 0.675 |

## Ablation 2: tool availability

With `check_policy_compliance` disabled on the 8 workflow items:

| Metric | All tools | Compliance tool disabled |
|---|---|---|
| Workflow completion | 100% | 25% |
| Partial match (workflow items) | 100% | 25% |
| Graceful responses | – | 100% |
| Action safety | 100% | 100% |
