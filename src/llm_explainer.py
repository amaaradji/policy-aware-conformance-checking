"""
llm_explainer.py
────────────────
Sends a conformance/compliance report to Azure OpenAI along with the BPMN model,
agg_log.xes (used for conformance checking), and enriched_log.xes (original log),
and returns a concise plain-language explanation.

Requires Azure OpenAI environment variables:
    AZURE_OPENAI_KEY=<your key>
    AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
    AZURE_OPENAI_DEPLOYMENT=<your deployment name, e.g. gpt-4o>
"""

import os
from openai import AzureOpenAI


# ── Azure OpenAI client ────────────────────────────────────────────────────

def _build_azure_client():
    """
    Returns (client, deployment_name) from Azure OpenAI environment variables.
    """
    azure_key      = os.environ.get("AZURE_OPENAI_KEY")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    azure_deploy   = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

    if not azure_key or not azure_endpoint:
        raise EnvironmentError(
            "\n\nAzure OpenAI credentials missing. Set these environment variables:\n\n"
            "    $env:AZURE_OPENAI_KEY='<your key>'\n"
            "    $env:AZURE_OPENAI_ENDPOINT='https://o3miniapi.cognitiveservices.azure.com/'\n"
            "    $env:AZURE_OPENAI_DEPLOYMENT='gpt-4.1'\n"
        )

    print(f"[llm_explainer] Using Azure OpenAI endpoint: {azure_endpoint}")
    print(f"[llm_explainer] Deployment: {azure_deploy}")

    client = AzureOpenAI(
        api_key        = azure_key,
        azure_endpoint = azure_endpoint,
        api_version    = "2024-12-01-preview",
    )
    return client, azure_deploy


# ── Main function with model and log context ───────────────────────────────

def explain_report_with_context(report_text: str, bpmn_content: str, agg_log_content: str, enriched_log_content: str) -> str:
    """
    Send the conformance report, BPMN model, agg_log.xes, and enriched_log.xes to Azure OpenAI
    and return a concise plain-language explanation.
    """
    client, deployment = _build_azure_client()

    # Minimal truncation - just enough to fit token limits
    MAX_CONTEXT = 8000
    if len(bpmn_content) > MAX_CONTEXT:
        bpmn_content = bpmn_content[:MAX_CONTEXT] + "\n... [truncated]"
    
    if len(agg_log_content) > MAX_CONTEXT:
        agg_log_content = agg_log_content[:MAX_CONTEXT] + "\n... [truncated]"
    
    enriched_log_limited = enriched_log_content[:5000]
    if len(enriched_log_content) > 5000:
        enriched_log_limited += "\n... [truncated]"

    # OPTIMIZED PROMPT - Short, focused, 3 sections only
    system_prompt = """You are a process mining expert. Analyze the provided data and answer with ONLY the 3 requested sections. Be concise. No introduction, no conclusion, no extra text."""

    user_prompt = f"""Analyze this conformance report and return EXACTLY these 3 sections:

## 1. PROBLEMATIC TRACES
List only traces with conformance < 1.000 or compliance < 1.000. For each: trace ID, conformance score, compliance score, and the specific issue.

## 2. CORRELATION ANALYSIS
Answer two questions:
- Is there correlation between conformance and compliance scores?
- Why does this correlation exist (or not exist)?

## 3. RECOMMENDATIONS FOR MODEL IMPROVEMENT
List 3-5 concrete, specific changes to the BPMN model based on the deviations found.

---
CONFORMANCE REPORT:
{report_text}

---
BPMN MODEL (excerpt):
{bpmn_content[:3000]}

---
AGGREGATED LOG (excerpt):
{agg_log_content[:3000]}
---

OUTPUT ONLY THE 3 SECTIONS ABOVE. BE CONCISE. USE BULLET POINTS."""

    response = client.chat.completions.create(
        model       = deployment,
        messages    = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature = 0.2,  # Lower temperature = faster, more consistent
        max_tokens  = 1000,  # Reduced from 2000
    )

    return response.choices[0].message.content


# ── Simpler version (just report, no context) ──────────────────────────────

def explain_report(report_text: str) -> str:
    """
    Simpler version: send only the report to Azure OpenAI.
    """
    client, deployment = _build_azure_client()

    system_prompt = "You are a process mining expert. Be concise. No extra text."

    user_prompt = f"""Analyze this conformance report and return EXACTLY 3 sections:

1. PROBLEMATIC TRACES: List traces with issues (ID, conformance, compliance, problem)
2. CORRELATION: Is there correlation between conformance and compliance? Why?
3. RECOMMENDATIONS: 3-5 specific improvements to the process model

Report:
{report_text}

OUTPUT ONLY THE 3 SECTIONS. USE BULLET POINTS. BE CONCISE."""

    response = client.chat.completions.create(
        model       = deployment,
        messages    = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature = 0.2,
        max_tokens  = 800,
    )

    return response.choices[0].message.content


# ── Standalone test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    sample_report = """
Trace 0: Conformance: 1.000, Compliance: 0.867, Total: 0.960
Trace 1: Conformance: 1.000, Compliance: 0.933, Total: 0.980
Trace 2: Conformance: 1.000, Compliance: 1.000, Total: 1.000
Trace 3: Conformance: 0.850, Compliance: 0.944, Total: 0.878
Trace 4: Conformance: 0.900, Compliance: 0.933, Total: 0.910
Trace 5: Conformance: 1.000, Compliance: 1.000, Total: 1.000
Trace 6: Conformance: 1.000, Compliance: 1.000, Total: 1.000
Trace 7: Conformance: 0.750, Compliance: 0.800, Total: 0.765
Trace 8: Conformance: 1.000, Compliance: 1.000, Total: 1.000
Trace 9: Conformance: 1.000, Compliance: 0.867, Total: 0.960
Overall score: 0.9453
"""
    print("=== Optimized LLM Response ===")
    print(explain_report(sample_report))