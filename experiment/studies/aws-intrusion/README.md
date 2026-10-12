# Study: AWS post-access behaviour (`aws-intrusion`)

What would an LLM propose as its next step after gaining access to an AWS account? Answers are coded
to CloudTrail events / MITRE ATT&CK to estimate the likely actions, per model — a companion dataset
to <https://traildiscover.cloud/> for the AI-driven case.

- `study.yaml` — settings (13 scenarios, 2 framings via the sibling `aws-intrusion-neutral/`, 20 reps).
- `PROTOCOL.md` — pre-registration: question, hypotheses, outcomes, coding, analysis plan.
- `codebook.md` — the coding scheme (tactic → ATT&CK → CloudTrail events) and resolved conventions.
- `scenarios/` — one decision point per file, across the kill chain (files starting with `_` ignored).
- `prompts/` — the shared system + user templates.

## Run it

```bash
# fake models, no cost / no account:
uv run llmtrials run studies/aws-intrusion --models ../../models/dummy.yaml --label dry-run
# real models (see the repo README for logins / API key):
uv run llmtrials run studies/aws-intrusion --models models/subscriptions.yaml --quota-cap 80
```

## Analyse (scripts live here)

```bash
uv run llmtrials export runs/aws-intrusion-assessment/<run_id> --blind   # blind review files
python draft_coding.py runs/aws-intrusion-assessment/<run_id>            # rule-based draft coding
python analyze.py      runs/aws-intrusion-assessment/<run_id>            # per-model rates + CT ranking
python aggregate.py                                                      # pooled cross-model tables
```

## Human-validate the draft coding

```bash
python review/build_review.py --per-cell 4      # -> review/coding_tool.html (blind) + review_key.csv
# open review/coding_tool.html in a browser, review each answer, Download coding -> human_coding.csv
python review/score_review.py review/human_coding.csv   # your-vs-draft agreement (kappa) + rates
```
Tactic kappa >= 0.80 means the draft rates are trustworthy to publish; lower means recode more
or tighten `codebook.md` first. The generated HTML/key are git-ignored (they embed answers).

Findings write-up: [`../../docs/detection-review.md`](../../docs/detection-review.md).
The draft coding is a starting point; see `PROTOCOL.md §8` for the human-coding step needed before
publishing numbers.
