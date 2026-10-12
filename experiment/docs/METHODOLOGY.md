# Methodology

## What is measured

Each trial shows a model a fixed **decision point**: a situation, the tools an agent would have, and a
request for the single next action. The model answers once. It gets no tool execution and no feedback
loop. We record the answer it **states**, which approximates the first action an agent built on that
model would take.

It does not measure behaviour over many steps (error recovery, drift, persistence). To get closer to
agent conditions without an agent, write the situation as the agent would see it. A scenario can
include a short transcript of earlier steps and observations; it is just text in the template.

## Design rules and how llmtrials enforces them

| Threat to validity | Control |
|---|---|
| Stimuli differ between models | One study = one set of rendered prompts. Inputs are hashed (`inputs_sha256`); `resume` refuses a modified snapshot. |
| Silent typos in configs | Unknown YAML keys are errors. Every `{{placeholder}}` must be filled and every scenario input used. |
| Sampling noise mistaken for a model difference | `repetitions` independent calls per scenario × model; analyse distributions, not single answers. |
| Time effects (load, rate limits, silent updates) confounded with conditions | Call order shuffled with a recorded `seed`; each model has its own lane. |
| Lost or partial data | Every trial is appended to `trials.jsonl` as it finishes; failures are recorded, not dropped; `resume` completes the plan. |
| Unknown software and model versions | Manifest: package versions, harness CLI versions, git commit, platform, dated model slugs (OpenRouter). Every record keeps the exact prompt and the raw response. |
| Harness context leaking into answers | Harnesses reduced to the minimum they allow; what remains is held constant, captured, fingerprinted per trial (`status` → `ctx`) and documented (below). |
| Subscription limits interrupting the run | Lanes pause at the limit and continue after the reset; pauses are logged in the manifest. |
| Rater bias | `export --blind`: shuffled items, no model labels, key in a separate file. |

## Access routes and what reaches the model

This project collects its main data through **subscriptions** (Claude Pro/Max, ChatGPT), which have no
per-token cost. Subscriptions can only be used through the vendors' agent harnesses. The condition
measured is therefore **"the model inside its vendor's agent harness, with tools disabled"**, which is
close to how an agent built on these products runs. The harness residue is part of the condition. It
must be constant across trials, recorded, and reported.

### Claude subscription: `claude_agent_sdk`

The Claude Agent SDK runs Claude Code (bundled; version pinned in `pyproject.toml`). Settings forced on
every trial:

- the study's system prompt replaces Claude Code's;
- `tools=[]`, no MCP servers, plugins or skills;
- `setting_sources=[]` (no settings, no CLAUDE.md), auto-memory off, no session files;
- one turn; `verbatim_prompts` on (no `@file` expansion or slash commands inside scenarios);
- a fixed, empty working directory, so the environment block is identical across trials.

**What still reaches the model.** Observed 2026-10-06 with claude-agent-sdk 0.2.163 / Claude Code
2.1.286, from the request captured in each trial (`meta.api_request`, PII-redacted):

| Added by Claude Code | Where |
|---|---|
| A billing-header text block, then "You are a Claude agent, built on Anthropic's Claude Agent SDK." | System prompt, before the study's own |
| The account's **email address** | `<system-reminder>` in the user turn |
| Environment: working directory, platform, shell, OS version | User turn (Haiku 4.5) or a separate system message after it (Sonnet 5.5) |
| The model's own name, id and knowledge cutoff | Same as above |
| Today's date | Same as above |
| Its own defaults: Sonnet 5.5 used adaptive thinking, effort `medium`, `max_tokens` 128000; Haiku 4.5 with thinking disabled used `temperature` 1 | Request parameters (llmtrials sets thinking/effort explicitly) |

None of this can be turned off with a subscription login (Claude Code's `--bare` mode would remove it,
but it accepts only API keys). Consequences:

- Each trial records `meta.harness_context`: a fingerprint of everything added around the prompt.
  Within a run it should stay constant per model; `llmtrials status` shows the number of variants
  (`ctx`). A second variant normally means the date changed during a long run.
- The model sees **today's real date and your OS**. See *Dates and times in scenarios* below.
- The email is personal data inside the prompt. It is redacted in saved records, but the model saw it.
  Another researcher reproducing the study sends their own email instead.
- Placement differs between Claude models, so two Claude models do not see byte-identical input.

**Policy.** Anthropic supports using your own Claude plan with the Agent SDK in your own projects.
Third-party products may not offer Claude.ai login. Usage counts against the plan's limits: a dedicated
monthly Agent SDK credit announced for 15 June 2026 was paused
([support article](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan));
check it again before a large run. `cost_usd` on this route is the API-price equivalent reported by
Claude Code, not money charged.

### ChatGPT subscription: `codex_sdk`

The official `openai-codex` Python SDK runs a bundled Codex CLI (version pinned). Settings forced on
every trial:

- an isolated `CODEX_HOME` (your normal Codex config, plugins and memories are never used);
- an ephemeral thread; the study's system prompt as `base_instructions`;
- environment/date, permissions, collaboration, apps, skills and multi-agent context all off;
- no AGENTS.md, memories or history;
- nested tools removed (shell, apply_patch, web search, plan, images, MCP, plugins);
- read-only sandbox, approvals denied.

**What still reaches the model.** Observed 2026-10-06 with openai-codex 0.160.1:

- A local render (`codex debug prompt-input`, run automatically at the start of each run, stored as
  `render_check` in the manifest and fingerprinted in each trial) shows **only the user message**: no
  date, no environment, no email.
- Current models are "code-mode only": they always receive Codex's `exec`/`wait` tool schemas. A
  trivial prompt (about 20 tokens of text) used 1,630 input tokens on this route. Tool use is detected
  (`tool_calls`, `meta.item_types`) and should be 0. It was 0 in every live test.
- Codex picks a per-model default reasoning effort (`low` for gpt-6.1-sol and gpt-6-astra), so `effort`
  is always set explicitly in the models file.
- No temperature, top_p or seed can be set.

**Policy.** OpenAI documents ChatGPT-login automation (`codex exec` / SDK) as an advanced option for
trusted private automation, with API keys as the default recommendation. Never share or commit
`auth.json`, and give each machine or job stream its own login
([non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[CI/CD auth](https://learn.chatgpt.com/docs/auth/ci-cd-auth)). `llmtrials login-codex` creates a
separate login for this purpose.

### Running on subscriptions

- **Limits.** Plans have a 5-hour and a weekly usage window, shared with your own interactive use of
  Claude and Codex. When a trial hits the limit, it is not recorded as a failure. The model's lane
  waits until the reported reset, then retries the same trial (`--on-limit stop` stops the lane
  instead; finish later with `resume`). Every pause is logged in the manifest (`sessions[].pauses`).
- **Keeping quota for yourself.** `--quota-cap 80` pauses a lane once any window reaches 80%, until
  that window resets. Each progress line shows current usage (`quota 5h 37% 7d 30%`), and
  `llmtrials status` shows the last reported values.
- **Throughput.** ChatGPT Plus allows roughly 5–45 (Astra), 15–160 (6.1 Sol) or 350–3,000 (Luna)
  Codex messages per 5 hours (OpenAI docs, Oct 2026). A study of a few hundred trials on frontier
  models takes days. Pilot on the fast tier.
- **Long runs and time.** Pauses spread a run over days. That is fine for the design: the call order is
  randomised, so time is not confounded with scenarios. But Claude's injected date changes, which
  `ctx` makes visible.
- **Before a run:** `llmtrials check models/subscriptions.yaml` verifies logins, plan, model
  availability, Codex's render check and current quota, without spending any.

### Dates and times in scenarios

On the Claude route the model is told today's real date, so a scenario saying "Saturday 03:10" can
contradict it. Either avoid absolute weekdays and dates (use "at night", "end of the working week"), or
state the scenario's date explicitly and treat any conflict as part of the stimulus. Codex sends no
date, so this affects only the Claude route.

### OpenRouter and other OpenAI-compatible APIs (optional)

A plain API call sends exactly one system message plus one user message, with no harness. Use it to:
- add model families that have no subscription route (open-weight models, Gemini, Grok...);
- or run a small **harness comparison**: the same model through the subscription and through the API
  on a subset of scenarios. `models/openrouter.yaml` has an API twin for each subscription model.

It is pay per token. At 2026-10-06 OpenRouter prices, a Sonnet-class model costs roughly 1–2 cents
per trial for prompts like the example study's (more with long reasoning). A 30-trial comparison
therefore costs under a dollar per model.

- Pin the upstream host with `provider: {only: [...]}`. The same open-weight model id is served by
  different companies at different precision (e.g. DeepSeek V4 Pro at fp4 or fp8 on 16 hosts, Kimi K3
  at fp4/fp8/mxfp4 on 24 hosts, checked 2026-10-06).
- `require_parameters: true` makes a call fail instead of silently dropping an unsupported parameter.
- At run start, the dated `canonical_slug` (e.g. `anthropic/claude-sonnet-5.5-20260928`) is recorded.

## Sampling parameters

Neither subscription route can set `temperature`, `top_p` or `seed`, so all models run with their
vendor's sampling defaults (on OpenRouter, also leave them unset for comparability). Set the same
reasoning effort explicitly for every model (`medium` in `models/subscriptions.yaml`), and report it.
If a study needs low-variance answers, add repetitions.

## Models, versions, hosts

- Subscription model ids are vendor aliases (`claude-sonnet-5-5`, `gpt-6.1-sol`) that can change
  behind the same name. Records keep what each response reports (`response.model`) and the harness
  version. On OpenRouter, the dated `canonical_slug` is recorded.
- Harness SDK versions are pinned exactly in `pyproject.toml`; upgrading them changes the condition.
  Do it between studies, not during one.
- Run all models of a comparison within a short window (days, not months), and do not mix runs made
  with different study versions.
- Note retirements: gpt-5.5 leaves Codex on 2026-10-14; OpenRouter lists `expiration_date`.

## Repetitions and sample size

Start with 5 repetitions × 10 or more scenarios per category in the pilot. Use the observed variance to
size the main run. Report uncertainty with confidence intervals that resample **scenarios** (not only
repetitions), since generalising across situations is the claim.

## Response format

Asking for a fixed JSON object makes answers easy to code, but format constraints can change model
behaviour, and compliance varies (in the first live test Haiku 4.5 wrapped its JSON in a code fence).
Choose deliberately in the protocol. Parse leniently and count non-compliant answers per model.

Reasoning summaries (`response.reasoning`, enabled in `models/subscriptions.yaml`) are recorded for
qualitative analysis. They are not comparable across vendors, so do not score the final answer from them.

## Evaluation

1. Write the rubric (categories, definitions, examples) in `PROTOCOL.md` before looking at outputs.
2. `llmtrials export <run> --blind`. Raters work from `blind.csv` (or `.md`) and never open
   `blind_key.csv`.
3. Double-rate a subset (e.g. 20–30%) with at least two raters, and report Krippendorff's α or
   Cohen's κ. Resolve disagreements by a rule written in advance.
4. An LLM judge may scale up coding only after its agreement with human ratings has been measured on
   held-out items. Do not use a tested model as its own judge.
5. Blinding is imperfect: some answers name their own model. Note how often that happens.

## Exclusions (decide in advance)

Provider errors are excluded but must be reported per model. Also decide how to handle:
- trials with `tool_calls > 0`;
- truncated answers (`finish_reason` length/max_tokens);
- unparseable answers;
- refusals, which are often a finding rather than an exclusion.

## Reporting checklist

- [ ] Study version and `inputs_sha256`; models file; run ids.
- [ ] Route per model, harness versions and the residue described above; `ctx` variants; pauses.
- [ ] Harness comparison against the API, if run.
- [ ] Model versions (dated ids / canonical slugs), hosts and precision, reasoning effort.
- [ ] Collection dates; package and CLI versions (from `manifest.json`).
- [ ] Repetitions, errors and exclusions per model.
- [ ] Rubric, raters, blinding, agreement statistic.
- [ ] Data and scenario release (licence) or the reason for not releasing.
