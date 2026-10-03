"""Alignments of the distinct sequences (variants) of a log in parallel worker processes, most
frequent variants first, so that a time budget covers as many cases as possible."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from . import core


def _align_share(task):
    model, sequences, trace_budget, total_budget = task
    return core.align(sequences, model, trace_budget, total_budget)


def align_variants(sequences, model, workers=6, trace_budget=30, total_budget=1200):
    """Per sequence, its alignment result (None if its variant exceeded a budget). Variants are
    dealt round-robin by decreasing frequency to `workers` processes; each process has
    `total_budget` seconds for its share and `trace_budget` seconds per variant."""
    counts = Counter(tuple(s) for s in sequences)
    variants = [v for v, _ in counts.most_common()]
    shares = [variants[i::workers] for i in range(workers)]
    tasks = [(model, [list(v) for v in share], trace_budget, total_budget) for share in shares]
    by_variant = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for share, results in zip(shares, pool.map(_align_share, tasks)):
            by_variant.update(zip(share, results))
    return [by_variant[tuple(s)] for s in sequences]
