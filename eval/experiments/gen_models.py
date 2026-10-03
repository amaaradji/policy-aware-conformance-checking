"""Process models for the scalability analysis (RQ3).

Process trees are drawn with the PTandLogGenerator (Jouck and Depaire) as implemented in pm4py,
per structural category (operator probabilities below; no loops, OR, silent, or duplicate
activities) and size (number of visible activities), and kept only when their BPMN translation
falls in the intended category. Each activity gets a transactional and a consumption property
drawn uniformly, its own resource, and the corrected policy templates (configs.py).
Output: models/generated/<category>_<size>_<replica>.bpmn and activity_info_<same>.json.
"""
import json
import random
import sys
from pathlib import Path

import pm4py
from pm4py.algo.simulation.tree_generator.variants import ptandloggenerator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import configs  # noqa: E402
from pacc_eval.paths import MODELS  # noqa: E402
from pacc_eval.profile import model_profile  # noqa: E402

CATEGORIES = {"Seq": {"sequence": 1.0, "choice": 0.0, "parallel": 0.0},
              "XOR": {"sequence": 0.5, "choice": 0.5, "parallel": 0.0},
              "AND": {"sequence": 0.5, "choice": 0.0, "parallel": 0.5},
              "Comb": {"sequence": 0.4, "choice": 0.3, "parallel": 0.3}}
SIZES = [5, 10, 25, 50, 100, 200]
REPLICAS = 3
OUT = MODELS / "generated"


def tree(category, size, seed):
    random.seed(seed)
    return ptandloggenerator.apply({"mode": size, "min": size, "max": size, "loop": 0.0, "or": 0.0,
                                    "silent": 0.0, "duplicate": 0.0, **CATEGORIES[category]})


def configuration(labels, seed):
    rng = random.Random(seed)
    entries = []
    for label in labels:
        entry = {"activity": label, "type": rng.choice(sorted(configs.ACTIVITY_TEMPLATES)),
                 "currentState": "NotActivated", "resource": f"Resource {label}",
                 "resourceType": rng.choice(sorted(configs.RESOURCE_TEMPLATES)),
                 "resource_current_state": "NotConsumed"}
        entries.append(configs.correct_activity(entry))
    return {"activities": entries}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for c, category in enumerate(CATEGORIES):
        for size in SIZES:
            for replica in range(REPLICAS):
                name = f"{category}_{size}_{replica}"
                path = OUT / f"{name}.bpmn"
                for attempt in range(1000):
                    seed = 1_000_000 * c + 1000 * size + 10 * replica + 100_000 * attempt
                    pt = tree(category, size, seed)
                    pm4py.write_bpmn(pm4py.convert_to_bpmn(pt), str(path))
                    found, n_act = model_profile(path)
                    if found == category and n_act == size:
                        break
                else:
                    raise RuntimeError(f"no {name} model found")
                labels = sorted({t.label for t in pm4py.convert_to_petri_net(pt)[0].transitions if t.label})
                with open(OUT / f"activity_info_{name}.json", "w", encoding="utf-8") as fh:
                    json.dump(configuration(labels, seed), fh, indent=1)
                print(name, "seed", seed, "attempts", attempt + 1, flush=True)


if __name__ == "__main__":
    main()
