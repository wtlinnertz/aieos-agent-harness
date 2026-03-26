# Shadow Agent Scan

## §1 Scan Methodology

Four methods used to identify undocumented AI agents operating outside harness governance:

1. **API key audit** — Enumerate all AI provider API keys configured in the environment and verify each corresponds to a documented provider adapter.
2. **Service account inventory** — Check for service accounts, bot tokens, or persistent credentials that could indicate autonomous agents.
3. **Network traffic analysis** — Review outbound network connections to identify calls to AI provider endpoints not configured in harness.yaml.
4. **Team interviews** — Confirm with all team members that no AI tools are being used outside the harness for AIEOS artifact work.

## §2 Scan Date

2026-03-26

## §3 Scan Results

**No shadow agents discovered.**

| Method | What Was Checked | Finding |
|--------|-----------------|---------|
| API key audit | Environment variables ANTHROPIC_API_KEY, OPENAI_API_KEY | Only configured providers found. No unexpected API keys in environment. |
| Service account inventory | System service accounts, cron jobs, daemon processes | No service accounts. Harness uses user-provided API keys at runtime only. |
| Network traffic analysis | Outbound connections from harness process | Harness only calls configured provider endpoints (api.anthropic.com, api.openai.com). No unexpected outbound connections. |
| Team interviews | N/A (solo project) | Developer confirms no agents running outside harness configuration. No additional AI tools used for artifact generation or validation. |

## §4 Disposition

No shadow agents found. All AI agents are inventoried in harness.yaml and documented in the practice assessment. Every AI provider interaction is routed through the harness adapter layer, logged in JSONL observability, and subject to the seven AIEOS invariants.

## §5 Scan Schedule

Re-scan when any of the following occur:

- New adapter added to `src/adapters/`
- New team members join the project
- New provider integrations configured in harness.yaml
- New environment variables for AI provider keys introduced

**Minimum cadence:** Quarterly, aligned with eval domain review cycle (next: 2026-06-26).
