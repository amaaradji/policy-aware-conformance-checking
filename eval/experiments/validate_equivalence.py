"""Check that eval/pacc_eval reproduces the published pipeline exactly.

The same observed log is checked twice: by the original scripts (policy_check.py,
agg_event.py, conf_check.py from PFE_interface, run unchanged as subprocesses on XES files)
and by the in-memory port. Per-event flags and per-trace (f, v, PACC) must match.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pm4py.objects.log.exporter.xes import exporter as xes_exporter
from pm4py.objects.log.importer.xes import importer as xes_importer
from pm4py.objects.log.obj import Event, EventLog, Trace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import core  # noqa: E402
from pacc_eval.paths import MODELS, REPO  # noqa: E402

ORIGINAL_SRC = REPO.parent / "PFE_interface" / "Backend" / "src"
LINE = re.compile(r"Trace (\S+): Conformance: ([\d.]+), Compliance: ([\d.]+), Total: ([\d.]+)")


def export(log, path):
    out = EventLog()
    for i, events in enumerate(log):
        trace = Trace(attributes={"concept:name": str(i)})
        for e in events:
            trace.append(Event(dict(e)))
        out.append(trace)
    xes_exporter.apply(out, str(path), parameters={"show_progress_bar": False})


def run_original(work, enriched_xes, model_id):
    (work / "src").mkdir(parents=True)
    (work / "logs").mkdir()
    for script in ("policy_check.py", "agg_event.py", "conf_check.py", "llm_explainer.py"):
        shutil.copy(ORIGINAL_SRC / script, work / "src" / script)
    shutil.copy(MODELS / "original" / f"processModel_{model_id}.bpmn", work / "src" / "processModel_1.bpmn")
    shutil.copy(MODELS / "original" / f"activity_info_{model_id}.json", work / "src" / "activity_info_1.json")
    shutil.copy(enriched_xes, work / "logs" / "enriched_log.xes")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AZURE_")}
    env["PYTHONIOENCODING"] = "utf-8"
    out = ""
    for script in ("policy_check.py", "agg_event.py", "conf_check.py"):
        proc = subprocess.run([sys.executable, str(work / "src" / script)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env, cwd=work)
        if proc.returncode != 0:
            raise RuntimeError(f"{script} failed:\n{proc.stderr[-2000:]}")
        out += proc.stdout
    scores = {m.group(1): tuple(float(x) for x in m.groups()[1:]) for m in LINE.finditer(out)}
    flagged = xes_importer.apply(str(work / "logs" / "enriched_log.xes"), parameters={"show_progress_bar": False})
    flags = [[(e.get("a_comp"), e.get("r_comp"), e.get("b_comp")) for e in trace] for trace in flagged]
    return scores, flags


def main(model_id=1, n_traces=60, seed=7):
    model = core.load_model(MODELS / "original" / f"processModel_{model_id}.bpmn")
    act_info = core.load_activity_info(MODELS / "original" / f"activity_info_{model_id}.json")
    log, policy_labels, flow_labels = core.generate(model, act_info, n_traces, 0.6, 0.3, seed)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        export(log, tmp / "observed.xes")
        orig_scores, orig_flags = run_original(tmp / "orig", tmp / "observed.xes", model_id)
        reimported = [[dict(e) for e in trace] for trace in
                      xes_importer.apply(str(tmp / "observed.xes"), parameters={"show_progress_bar": False})]

    f, v, pacc, _ = core.run_pacc(reimported, act_info, model)
    port_flags = [[(e["a_comp"], e["r_comp"], e["b_comp"]) for e in trace] for trace in reimported]

    flag_mismatch = sum(a != b for ta, tb in zip(orig_flags, port_flags) for a, b in zip(ta, tb))
    score_mismatch = [
        (str(i), orig_scores.get(str(i)), (round(f[i], 3), round(v[i], 3), round(pacc[i], 3)))
        for i in range(len(reimported))
        if orig_scores.get(str(i)) != (round(f[i], 3), round(v[i], 3), round(pacc[i], 3))
    ]
    print(f"model {model_id}: {len(reimported)} traces, {sum(map(len, reimported))} events, "
          f"{len(policy_labels)} policy and {len(flow_labels)} control-flow injections")
    print(f"per-event flag mismatches: {flag_mismatch}")
    print(f"per-trace score mismatches: {len(score_mismatch)}")
    for row in score_mismatch[:5]:
        print("   ", row)
    return flag_mismatch == 0 and not score_mismatch


if __name__ == "__main__":
    ok = all(main(m) for m in (1, 5, 13))
    print("EQUIVALENT" if ok else "NOT EQUIVALENT")
    sys.exit(0 if ok else 1)
