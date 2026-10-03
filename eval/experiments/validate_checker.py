"""Validation of the re-implemented policy-compliance pathway (eval/pacc_eval/checker.py).

1. Compiled policies: the guarded transition relation of every (instantiated) template.
2. No false alarms (Property P3): on clean logs of the 20 models, v = 1 and f = 1 for every
   trace under the full semantics.
3. Agreement with the published checker: with guards, initial-state and strict-presence checks,
   and synchronization off, the per-event verdicts equal those of the published checker's loop
   (src/policy_check.py, ported in core.check_policies) on logs with injected policy violations.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core  # noqa: E402
from pacc_eval.paths import MODELS, POLICIES  # noqa: E402

N_MODELS = 20


def show_templates():
    for folder in ("activity_policies", "resource_policies", "binding_policies"):
        for path in sorted((POLICIES / folder).glob("*.json")):
            policy = checker.compile_policy(configs.template(folder, path.name))
            rules = [f"{s}->{t}" + (f" {[list(map(str, g)) for g in gs if g]}" if any(gs) else "")
                     for (s, t), gs in policy.allowed.items()]
            rules += [f"sync {y.source}->{y.target} while {y.activity_state} {[str(c) for c in y.guard]}"
                      for y in policy.sync]
            print(f"{folder}/{path.name}: " + "; ".join(rules))


def models():
    for m in range(1, N_MODELS + 1):
        model = core.load_model(MODELS / "original" / f"processModel_{m}.bpmn")
        published = core.load_activity_info(MODELS / "original" / f"activity_info_{m}.json")
        yield m, model, configs.corrected_activity_info(published)


def clean_logs(seeds=(1, 2, 3), n=100):
    bad_v = bad_f = total = 0
    reasons = Counter()
    for m, model, config in models():
        for seed in seeds:
            log, _, _ = core.generate(model, config, n, 0.0, 0.0, seed=seed * 100 + m)
            f, v, _, why = core.run_formal(log, config, model)
            bad_v += sum(x < 1 for x in v)
            bad_f += sum(x < 1 for x in f)
            total += len(log)
            reasons.update((r["a"] or r["r"] or r["b"]).split(" ")[0] for r in why)
    print(f"clean traces: {total}; with v < 1: {bad_v}; with f < 1: {bad_f}; reasons: {dict(reasons)}")
    return bad_v == bad_f == 0


def agreement(seeds=(1, 2, 3), n=100, rate=0.5):
    events = mismatches = 0
    examples = []
    for m, model, config in models():
        for seed in seeds:
            log, labels, _ = core.generate(model, config, n, rate, 0.0, seed=seed * 100 + m)
            snapshot = [[dict(e) for e in t] for t in log]
            core.check_policies(snapshot, config, per_instance=True)
            for t, (trace, ported) in enumerate(zip(log, snapshot)):
                flags, _ = checker.check_trace(trace, config, checker.PUBLISHED)
                for k, (mine, e) in enumerate(zip(flags, ported)):
                    if mine is None:
                        continue
                    events += 1
                    theirs = (e["a_comp"], e["r_comp"], e["b_comp"])
                    if mine != theirs:
                        mismatches += 1
                        if len(examples) < 5:
                            examples.append((m, seed, t, k, mine, theirs))
    print(f"published-mode agreement: {events - mismatches} of {events} events identical; examples: {examples}")
    return mismatches == 0


if __name__ == "__main__":
    show_templates()
    ok = clean_logs()
    ok = agreement() and ok
    print("VALID" if ok else "NOT VALID")
