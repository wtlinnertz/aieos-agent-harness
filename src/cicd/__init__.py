"""Spec-driven CI/CD capability substrate for the AIEOS agent harness.

Scope (M2):
  - Capability registry (artifact-store-backed, in-memory index per process)
  - Attestation verification at registration time
  - Tool-using agent interface (agent-with-LLM and agent-without-LLM variants)
  - Structured run-log emission (stdout JSON events)

This subpackage is distinct from src/adapters/, which holds LLM provider
adapters (Anthropic, OpenAI, Tool, Mock). The adapters here are CI/CD
capability adapters (adapter-pytest-unit, adapter-cosign-sign, etc.) —
they have contracts, conformance attestations, and registry entries.
"""
