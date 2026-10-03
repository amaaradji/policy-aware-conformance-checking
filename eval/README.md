# Evaluation package of the revised manuscript

"Policy-Aware Conformance Checking in Business Process Mining" (revised version, October 2026).
This folder reproduces every result of the revised evaluation. The original pipeline scripts in
`src/` are kept unchanged for reference.

## Setup

Python 3.11 on Windows, Linux, or macOS.

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Linux/macOS: .venv/bin/python
```

The real-life logs are not stored in the repository. Place them in a `data` folder next to the
repository (`../data` from the repository root):

- `data/bpic2013/BPI_Challenge_2013_incidents.xes.gz` (BPI Challenge 2013, incidents,
  4TU.ResearchData, https://doi.org/10.4121/uuid:500573e6-accc-4b0c-9576-aa5468b10cee)
- `data/bpic2019/BPI_Challenge_2019.xes` (BPI Challenge 2019, 4TU.ResearchData,
  https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1)

The LLM experiment needs Azure OpenAI credentials in the environment: `AZURE_OPENAI_KEY`,
`AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT` (default `gpt-4.1`). Never commit them.

## Layout

| Path | Content |
|------|---------|
| `pacc_eval/checker.py` | Policy-compliance pathway implementing the formal model (Section 4.4): ODRL compilation into guarded transition relations, activity instances, the activity, resource, and binding predicates with consumption episodes, Eq. (1). `Semantics` switches parts off for the ablations. |
| `pacc_eval/core.py` | Seeded generation (playout, lifecycle enrichment, policy and control-flow injection), the control-flow pathway (alignments on the instance projection), PACC, and a port of the published checker loop used for validation. |
| `pacc_eval/configs.py` | Corrected policy configuration: templates per transactional and consumption property, placeholder instantiation (5-minute binding bound, 1-hour asset duration). |
| `pacc_eval/oracle.py` | Independent admissibility oracle (lifecycle automata, not policies) for the mutation analysis. |
| `pacc_eval/llm.py` | LLM explanation (prototype and revised prompts) and the automatic faithfulness checks. |
| `pacc_eval/parallel.py` | Alignments of the distinct sequences in worker processes, most frequent first, within time budgets (real logs). |
| `experiments/` | One script per experiment (below). |
| `analysis/` | Tables (`results/tables`) and figures (`figures`) from the raw results. |
| `results/` | Raw per-trace and per-measurement results, logs, LLM explanations and their inputs. |
| `../models/` | `original` (the 20 evaluation models), `usecase` (model 21 and the hand-built log), `generated` (72 models for scalability), `bpic2013`. |

## Reproducing the results

Run from the repository root with the virtual environment's Python.

| Result | Command | Output |
|--------|---------|--------|
| Equivalence of the evaluation code with the published scripts | `eval/experiments/validate_equivalence.py` | console |
| Checker validation (no false alarms; published verdicts reproduced) | `eval/experiments/validate_checker.py` | console |
| RQ1 and RQ2, mixed design | `eval/experiments/exp_synthetic.py --design mixed` | `results/synthetic_mixed_traces.csv` |
| RQ1, per violation type | `eval/experiments/exp_synthetic.py --design types` | `results/synthetic_types_traces.csv` |
| Use case | `eval/experiments/exp_usecase.py` | `results/usecase_report.json`, `results/usecase_events.csv`, `models/usecase/usecase_log.xes` |
| Generated models | `eval/experiments/gen_models.py` | `models/generated/` |
| RQ3 | `eval/experiments/exp_scalability.py original|models|traces|parallel` | `results/runtime_*.csv` |
| BPI Challenge 2013 | `eval/experiments/exp_bpic2013.py` | `results/bpic2013_*` |
| BPI Challenge 2019 | `eval/experiments/bpic2019_prepare.py`, then `eval/experiments/exp_bpic2019.py` | `results/bpic2019_*` |
| RQ4 | `eval/experiments/exp_llm.py` (`--assess-only` re-scores saved explanations) | `results/llm/`, `results/llm_faithfulness.csv` |
| Tables and figures | `eval/analysis/detection.py`, `weights.py`, `runtime.py`, `llm_faithfulness.py`, `figures.py` | `results/tables/`, `figures/` |

All randomness is seeded; timings depend on the machine (the reported ones come from an Intel Core
i7-10750H laptop with 32 GB of RAM).

## Differences from the prototype of the submitted version

The re-implementation was validated against the prototype (identical verdicts on 171,550 events
with the extensions below disabled). The extensions, all specified by the formal model, are:

1. Policy configuration per consumption property, with both consumption cycles of limited
   resources (the prototype assigned templates that did not cover the resource lifecycles and
   flagged most compliant traces).
2. Lifecycle state per activity instance rather than per activity name (loops).
3. Fitness on the activity-instance projection, and v exactly as Eq. (1).
4. ODRL constraints are evaluated (count, elapsed time, dates, order relative to the policy usage,
   precedence among activities, asset durations); the prototype resolved no constraint operand.
5. Binding policies synchronize the resource's consumption episode with the activity's state; the
   prototype checked only the identity of the resource.
6. A missing or unparseable policy fails its evaluation; an instance must start in the initial
   states of its lifecycles.
7. The checked log is never modified (the prototype's interface renamed 20% of its events).
