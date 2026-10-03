"""Evaluation framework for policy-aware conformance checking (PACC).

The policy-compliance pathway reuses the published checker in ``src/policy_check.py``
unchanged; enrichment, aggregation, and scoring replicate ``src/enrich_event.py``,
``src/agg_event.py``, and ``src/conf_check.py`` in memory, with explicit random seeds.
"""
