# Policy-aware conformance checking

Implementation and evaluation of "Policy-Aware Conformance Checking in Business Process Mining"
(Maamar, Maaradji, Nehari, and Benna).

The approach complements an event log with a policy log: activities carry transactional
properties (pivot, compensatable, retriable), resources carry consumption properties (shareable or
not; unlimited, limited, or limited but renewable), and ODRL policies govern the lifecycle of
activities, of resource consumptions, and of their bindings. A trace is checked along two pathways,
alignment-based fitness on the process model and policy compliance on the policy log, combined in
the PACC score.

| Folder | Content |
|--------|---------|
| `eval/` | Evaluation package of the revised manuscript: checker implementing the formal model, generators, experiments, analysis, and raw results. Start with [`eval/README.md`](eval/README.md). |
| `policies/` | ODRL policy templates (activity, resource, and binding policies). |
| `config/` | Lifecycles of the transactional and consumption properties. |
| `models/` | BPMN models and their policy configurations. |
| `src/` | Pipeline scripts of the submitted version (kept for reference). |

Setup: `python -m venv .venv`, then `pip install -r requirements.txt` (Python 3.11).
