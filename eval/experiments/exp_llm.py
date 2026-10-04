"""Faithfulness of the LLM explanations.

Scenarios: the use-case log, and for one model per structural category (the largest), logs of
10 traces at three noise levels with violations of all seven types and control-flow deviations.
Each report is explained `--reps` times by each prompt variant (prototype prompt verbatim, and
the revised prompt whose policy reference follows the formal model). Every explanation is
checked against the checker's output and the optimal alignments: traces claimed per section,
activities named in control-flow claims, (trace, activity) and policy dimension of every policy
claim, unsupported trace ids, entities and numbers, and completeness.
Outputs: eval/results/llm/<variant>/<scenario>_<rep>.html (+ .json call metadata),
eval/results/llm/inputs/<scenario>.json, and eval/results/llm_faithfulness.csv.
`--assess-only` re-scores saved explanations without calling the model.
Credentials: AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT.
"""
import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from exp_usecase import build  # noqa: E402
from pacc_eval import checker, configs, core, llm  # noqa: E402
from pacc_eval.paths import MODELS, RESULTS  # noqa: E402
from pacc_eval.profile import model_profile  # noqa: E402

TYPES = core.VIOLATION_TYPES + ["premature_completion"]
NOISE = (0.0, 0.3, 0.6)
OUT = RESULTS / "llm"


def representative_models():
    best = {}
    for bpmn in sorted(MODELS.glob("original/processModel_*.bpmn"), key=lambda p: int(p.stem.split("_")[1])):
        category, n_act = model_profile(bpmn)
        if category not in best or n_act > best[category][1]:
            best[category] = (bpmn, n_act)
    return {c: b for c, (b, _) in sorted(best.items())}


def scenarios():
    config = {n: configs.instantiate(e) for n, e in
              core.load_activity_info(MODELS / "usecase" / "activity_info_21.json").items()}
    yield "usecase", list(build(config).values()), config, core.load_model(MODELS / "usecase" / "processModel_21.bpmn")
    for category, bpmn in representative_models().items():
        m = int(bpmn.stem.split("_")[1])
        model = core.load_model(bpmn)
        config = configs.corrected_activity_info(core.load_activity_info(bpmn.with_name(f"activity_info_{m}.json")))
        for k, noise in enumerate(NOISE):
            log, _, _ = core.generate(model, config, 10, noise, noise, seed=4000 + 10 * m + k,
                                      policy_types=TYPES, policy_weights=[1] * len(TYPES))
            yield f"{category}_m{m}_n{int(noise * 100)}", log, config, model


def prepare():
    (OUT / "inputs").mkdir(parents=True, exist_ok=True)
    cases = {}
    for name, log, config, model in scenarios():
        f, v, pacc, reasons = core.run_formal(log, config, model)
        projections = [checker.instance_projection(events, config) for events in log]
        moves = [r["alignment"] for r in core.align(projections, model)]
        deviations = [core.involved_activities(m) for m in moves]
        labels = set(config) | {a for p in projections for a in p}
        inputs = llm.report_inputs(log, config, list(config), f, v, pacc, reasons, moves)
        cases[name] = dict(n=len(log), config=config, f=f, v=v, reasons=reasons, inputs=inputs,
                           deviations=deviations, labels=labels)
        saved = OUT / "inputs" / f"{name}.json"
        if saved.exists():  # explanations were produced from these inputs: score against them
            cases[name]["inputs"] = json.loads(saved.read_text(encoding="utf-8"))["inputs"]
            continue
        with open(saved, "w", encoding="utf-8") as fh:
            json.dump({"inputs": inputs, "f": f, "v": v, "pacc": pacc, "reasons": reasons, "moves": moves}, fh, indent=1)
    return cases


def score(name, variant, rep, case, html, meta):
    row = {"scenario": name, "variant": variant, "rep": rep, **meta, "traces": case["n"],
           "flow_traces": sum(x < 1 for x in case["f"]), "policy_traces": sum(x < 1 for x in case["v"])}
    seen = {k: x for k, x in case["inputs"].items() if "{" + k + "}" in llm.VARIANTS[variant][1]}
    row.update(llm.assess(html, case["n"], case["f"], case["v"], case["reasons"], case["config"],
                          seen, case["deviations"], case["labels"]))
    return row


def main(reps, workers, assess_only, variants):
    cases = prepare()
    jobs = [(name, variant, rep) for name in cases for variant in llm.VARIANTS for rep in range(reps)]
    if not assess_only:
        client, deployment = llm.client()

        def call(job):
            name, variant, rep = job
            html, latency, p_tok, c_tok, model_id = llm.explain(client, deployment, cases[name]["inputs"], variant)
            (OUT / variant).mkdir(exist_ok=True)
            (OUT / variant / f"{name}_{rep}.html").write_text(html, encoding="utf-8")
            meta = {"model_id": model_id, "latency_s": round(latency, 3), "prompt_tokens": p_tok,
                    "completion_tokens": c_tok}
            (OUT / variant / f"{name}_{rep}.json").write_text(json.dumps(meta), encoding="utf-8")
            print(name, variant, rep, flush=True)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(call, [j for j in jobs if j[1] in variants]))
    rows = []
    for name, variant, rep in jobs:
        html = (OUT / variant / f"{name}_{rep}.html").read_text(encoding="utf-8")
        meta = json.loads((OUT / variant / f"{name}_{rep}.json").read_text(encoding="utf-8"))
        rows.append(score(name, variant, rep, cases[name], html, meta))
    with open(RESULTS / "llm_faithfulness.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} explanations scored")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--assess-only", action="store_true")
    ap.add_argument("--variants", nargs="+", default=list(llm.VARIANTS))
    args = ap.parse_args()
    main(args.reps, args.workers, args.assess_only, args.variants)
