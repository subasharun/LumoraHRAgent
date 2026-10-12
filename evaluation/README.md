# Evaluation

| File | Contents |
|---|---|
| `eval_set.json` | 30 questions/tasks: policy (8), multi-document (4), workflows (8), ambiguous (3), out-of-scope (3), failure handling (1), safety (3). Each has a gold answer, gold documents/sections, key facts, expected tools and expected behaviour. |
| `heldout_set.json` | 10 paraphrased items run once after development to check for overfitting. |
| `run_eval.py` | Runs the agent (in-process or against a URL), scores it, runs ablations, writes reports. Metric definitions are in its docstring. |
| `results/rules/` | Dev-set results with the offline planner, retrieval and tool-availability ablations, cold/warm start. |
| `results/heldout-rules-first-run/`, `results/heldout-rules-after-fix/` | Held-out results before and after the one generic fix. |

```bash
python evaluation/run_eval.py --mode rules --ablations --cold-start
python evaluation/run_eval.py --mode llm                                   # needs LLM_API_KEY
python evaluation/run_eval.py --base-url https://<app>.onrender.com        # deployed app
python evaluation/run_eval.py --set evaluation/heldout_set.json --mode rules --out evaluation/results/heldout
```

Each run writes `results.json` (per item, including the full answer), `summary.json`, and `report.md`. Dates in the gold answers assume `APP_TODAY=2026-10-12`, which the script sets by default.
