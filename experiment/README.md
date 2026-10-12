# experiment — run the test yourself

This folder is everything needed to reproduce the study: the `llmtrials` harness, the AWS
post-compromise study (prompts, 13 situations, codebook), and the scripts that turn raw runs into the
dataset the site reads.

The idea is simple. Ask a model, single-turn, what it would do next in a given situation. Record the
plan. Never execute anything. Then code each step to a MITRE ATT&CK tactic and the CloudTrail event it
would generate.

## Layout

- `src/llmtrials/` — the harness: providers (Claude via subscription, GPT via Codex, others via
  OpenRouter), the runner, de-duplication, and a privacy scrub.
- `models/` — model lists (one YAML per group).
- `studies/aws-intrusion/` — the study: `study.yaml`, `PROTOCOL.md`, `codebook.md`, `prompts/`,
  `scenarios/` (the 13 situations), plus the coding and analysis scripts.
- `studies/aws-intrusion-neutral/` — the same situations under a neutral "operator" framing (the
  second arm).
- `docs/` — methodology, plan, and the detection write-up.
- `runs/` — raw run outputs (git-ignored by default; large and can carry identity).

## Reproduce

```bash
# 1. install uv (https://docs.astral.sh/uv/), then from this folder:
cp .env.example .env            # add OPENROUTER_API_KEY for the OpenRouter models
uv run llmtrials login-claude   # optional: Claude via your Pro/Max subscription
uv run llmtrials login-codex    # optional: GPT via your ChatGPT subscription

# 2. check everything is reachable
uv run llmtrials check models/openrouter.yaml

# 3. run the study (example: one model, the assessment arm)
uv run llmtrials run studies/aws-intrusion --models models/openrouter.yaml --model deepseek-v4-pro@openrouter

# 4. code + aggregate + build the dataset the site reads
uv run python studies/aws-intrusion/aggregate.py          # cross-model tables (read-only)
uv run python studies/aws-intrusion/viz_data.py           # writes ../data/viz_data.json
uv run python studies/aws-intrusion/build_dashboard.py    # writes ../website/index.html
```

Add your own situations by dropping a YAML in `studies/aws-intrusion/scenarios/`, or point the same
harness at a different study folder. Re-run step 4 and the site picks up the new data.

## Notes

- Claude and GPT run through consumer subscriptions, so they must run from a normal terminal, not
  inside another Claude Code session (the session's environment leaks in). The provider warns if it
  detects this.
- Nothing is ever executed against a real AWS account. The model states a plan; the harness records it.
- Every saved record is run through a scrub that removes the account email and the identity tokens the
  harness injects, so shared data carries no personal identifiers.

## Free-text handling

A few models answer in prose with the JSON plan at the end; the coder now extracts that trailing JSON.
The small remainder of genuinely prose answers were coded by two models (Opus 4.6 + GPT-5 via
OpenRouter) with human adjudication of disagreements, recorded in
`studies/aws-intrusion/review/recovered.csv` (committed, read by the loader). Rebuild with
`studies/aws-intrusion/review/extract_freetext.py`. Anything still unparseable is flagged as free-text
and excluded from the rates.
