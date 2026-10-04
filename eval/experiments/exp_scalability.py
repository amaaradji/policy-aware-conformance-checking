"""Execution time of the two pathways. Only checking is timed: logs are generated before
the clock starts. Each measurement is preceded by an untimed warm-up run and garbage collection;
repetitions are reported individually (median and spread are computed in the analysis).

Parts:
  original  the 20 evaluation models, 1,000 traces, the five noise levels of the paper;
  models    generated models (4 categories x 6 sizes x 3 replicas), 200 traces, noise 0.1;
  traces    one model per category (10 activities) and the 6-activity Comb model, 1,000 to
            20,000 traces, noise 0.1;
  parallel  policy pathway over trace partitions and alignments over variants with 1 to 6
            worker processes, on the 20,000-trace logs.
Alignments run with a per-sequence budget (--trace-budget seconds) and a per-log budget
(--log-budget seconds); sequences that exceed them are counted, not aligned.
Output: eval/results/runtime_<part>.csv
"""
import argparse
import csv
import gc
import os
import platform
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pacc_eval import checker, configs, core  # noqa: E402
from pacc_eval.paths import MODELS, RESULTS  # noqa: E402
from pacc_eval.profile import model_profile  # noqa: E402

FIELDS = ["part", "model", "category", "activities", "traces", "events", "variants", "noise", "workers",
          "pathway", "rep", "seconds", "unaligned"]


def timed(fn, *args):
    gc.collect()
    start = time.perf_counter()
    result = fn(*args)
    return time.perf_counter() - start, result


def measure(writer, row, log, config, model, reps, align_reps, trace_budget, log_budget):
    projections = [checker.instance_projection(events, config) for events in log]
    row = dict(row, traces=len(log), events=sum(map(len, log)), variants=len({tuple(p) for p in projections}),
               workers=1)
    core.policy_pathway(log[: min(len(log), 50)], config)  # warm-up (policy compilation caches)
    for rep in range(reps):
        seconds, _ = timed(core.policy_pathway, log, config)
        writer.writerow(dict(row, pathway="policy", rep=rep, seconds=round(seconds, 4), unaligned=0))
    for rep in range(align_reps):
        seconds, results = timed(core.align, projections, model, trace_budget, log_budget)
        writer.writerow(dict(row, pathway="alignment", rep=rep, seconds=round(seconds, 4),
                             unaligned=sum(r is None for r in results)))


def original(writer, args):
    folder = MODELS / "original"
    for m in range(1, 21):
        bpmn = folder / f"processModel_{m}.bpmn"
        category, n_act = model_profile(bpmn)
        model = core.load_model(bpmn)
        config = configs.corrected_activity_info(core.load_activity_info(folder / f"activity_info_{m}.json"))
        for noise in (0.0, 0.05, 0.1, 0.2, 0.3):
            log, _, _ = core.generate(model, config, 1000, noise, noise, seed=7000 + m)
            measure(writer, {"part": "original", "model": m, "category": category, "activities": n_act,
                             "noise": noise}, log, config, model, args.reps, args.align_reps, args.trace_budget,
                    args.log_budget)
        print("original model", m, flush=True)


def generated(name):
    bpmn = MODELS / "generated" / f"{name}.bpmn"
    config = core.load_activity_info(MODELS / "generated" / f"activity_info_{name}.json")
    return core.load_model(bpmn), config


def models(writer, args):
    for path in sorted((MODELS / "generated").glob("*.bpmn"), key=lambda p: (p.stem.split("_")[0], int(p.stem.split("_")[1]), p.stem)):
        category, size, replica = path.stem.split("_")
        model, config = generated(path.stem)
        log, _, _ = core.generate(model, config, args.traces or 200, 0.1, 0.1, seed=8000 + int(size) + int(replica))
        reps = args.reps if int(size) <= 50 else max(3, args.reps // 2)
        align_reps = args.align_reps if int(size) <= 50 else 1
        measure(writer, {"part": "models", "model": path.stem, "category": category, "activities": int(size),
                         "noise": 0.1}, log, config, model, reps, align_reps, args.trace_budget, args.log_budget)
        print(path.stem, flush=True)


def trace_models():
    yield "Comb_m5", "Comb", *original_model(5)
    for category in ("Seq", "XOR", "AND", "Comb"):
        model, config = generated(f"{category}_10_0")
        yield f"{category}_10_0", category, model, config


def original_model(m):
    folder = MODELS / "original"
    return (core.load_model(folder / f"processModel_{m}.bpmn"),
            configs.corrected_activity_info(core.load_activity_info(folder / f"activity_info_{m}.json")))


def traces(writer, args):
    for name, category, model, config in trace_models():
        n_act = len(config)
        for n in (1000, 2000, 5000, 10000, 20000):
            log, _, _ = core.generate(model, config, n, 0.1, 0.1, seed=9000 + n)
            measure(writer, {"part": "traces", "model": name, "category": category, "activities": n_act,
                             "noise": 0.1}, log, config, model, args.reps, args.align_reps, args.trace_budget,
                    args.log_budget)
            del log
            print(name, n, flush=True)


def _partition_worker(name, i, n, reps, ready, go, queue):
    """Generate partition i (untimed), report ready, then check it once per repetition, when the
    main process releases that repetition. Errors are reported through the queues."""
    try:
        model, config = original_model(5) if name == "Comb_m5" else generated(name)
        log, _, _ = core.generate(model, config, n, 0.1, 0.1, seed=9500 + i)
        core.policy_pathway(log[:20], config)
        gc.collect()
        ready.put(i)
        for rep in range(reps):
            go[rep].wait()
            start = time.time()
            core.policy_pathway(log, config)
            queue.put((start, time.time()))
            gc.collect()
    except BaseException:
        ready.put(-1)
        queue.put(("error", i, traceback.format_exc()))


def _policy_spans(name, workers, total, reps, attempts=3, wait=1800):
    """Wall-clock span (first start to last end) of each repetition over `workers` processes; all
    workers start a repetition together, released by the main process."""
    import multiprocessing as mp
    from queue import Empty
    for attempt in range(attempts):
        ready, queue, go = mp.Queue(), mp.Queue(), [mp.Event() for _ in range(reps)]
        procs = [mp.Process(target=_partition_worker, args=(name, i, total // workers, reps, ready, go, queue))
                 for i in range(workers)]
        for proc in procs:
            proc.start()
        spans, problem = [], None
        try:
            if any(ready.get(timeout=wait) < 0 for _ in range(workers)):
                problem = queue.get(timeout=60)[2]
            for rep in range(reps if problem is None else 0):
                go[rep].set()
                items = [queue.get(timeout=wait) for _ in range(workers)]
                errors = [x for x in items if isinstance(x[0], str)]
                if errors:
                    problem = errors[0][2]
                    break
                spans.append((min(s for s, _ in items), max(e for _, e in items)))
        except Empty:
            problem = "timeout waiting for the workers"
        for event in go:
            event.set()
        for proc in procs:
            proc.join(timeout=120)
            if proc.is_alive():
                proc.terminate()
        if problem is None:
            return spans
        with open(RESULTS / "parallel_errors.log", "a", encoding="utf-8") as fh:
            fh.write(f"{name} workers={workers} attempt={attempt}\n{problem}\n")
        gc.collect()
    raise RuntimeError(f"policy pathway with {workers} workers failed {attempts} times for {name}")


_MODELS = {}


def _align_variants(task):
    """Align a share of the distinct sequences (worker side; the model is loaded once per process)."""
    name, sequences = task
    if name not in _MODELS:
        _MODELS[name] = (original_model(5) if name == "Comb_m5" else generated(name))[0]
    return core.align(sequences, _MODELS[name])


def parallel(writer, args):
    """Policy pathway over w partitions of 20,000 / w traces in w processes (wall time from the
    first start to the last end), and pm4py's process-pool alignments over the variants."""
    from concurrent.futures import ProcessPoolExecutor
    total = args.traces or 20000
    for name, category, model, config in trace_models():
        log, _, _ = core.generate(model, config, total, 0.1, 0.1, seed=9000 + total)
        projections = [checker.instance_projection(events, config) for events in log]
        base = {"part": "parallel", "model": name, "category": category, "activities": len(config),
                "traces": total, "events": sum(map(len, log)), "variants": len({tuple(p) for p in projections}),
                "noise": 0.1}
        del log
        for workers in (1, 2, 4, 6):
            for rep, (first, last) in enumerate(_policy_spans(name, workers, total, args.reps)):
                writer.writerow(dict(base, workers=workers, pathway="policy", rep=rep, unaligned=0,
                                     seconds=round(last - first, 4)))
            variants = sorted({tuple(p) for p in projections})
            shares = [(name, [list(v) for v in variants[i::workers]]) for i in range(workers)]
            with ProcessPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_align_variants, [(name, [list(variants[0])])] * workers))  # load the model
                for rep in range(args.align_reps):
                    gc.collect()
                    start = time.perf_counter()
                    list(pool.map(_align_variants, shares))
                    writer.writerow(dict(base, workers=workers, pathway="alignment", rep=rep, unaligned=0,
                                         seconds=round(time.perf_counter() - start, 4)))
            print(name, workers, flush=True)


PARTS = {"original": original, "models": models, "traces": traces, "parallel": parallel}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=list(PARTS))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--align-reps", type=int, default=3)
    ap.add_argument("--traces", type=int, default=None)
    ap.add_argument("--trace-budget", type=float, default=60.0)
    ap.add_argument("--log-budget", type=float, default=1800.0)
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"runtime_{args.part}.csv"
    with open(out, "w", newline="", encoding="utf-8", buffering=1) as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        print(f"# {platform.processor()} | {os.cpu_count()} logical CPUs | Python {platform.python_version()}",
              flush=True)
        PARTS[args.part](writer, args)
    print(out)
