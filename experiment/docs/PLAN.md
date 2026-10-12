# Plan

Goal: measure what LLMs propose as the **next step** in a given situation, i.e. how an agent built on
each model would probably act, without running an agent loop. The setup must be repeatable, valid as
research, and easy for others to rerun with other inputs or other models.

## 1. Review of the proposal

**What holds up**

- Asking the model for its next action at a fixed decision point, with no agent loop and no tool
  execution, isolates the model's judgement and is repeatable.
- Using the subscriptions you already pay for (Claude, ChatGPT) makes a study with many repetitions
  affordable. OpenRouter adds other model families when needed.
- Python plus plain YAML/Markdown inputs is easy to share and to review.

**Where it needs adjusting**

1. **Subscriptions only exist inside agent harnesses.** Programmatic access to a Claude or ChatGPT plan
   goes through the vendor's agent: Claude Code (via the Claude Agent SDK) or Codex (via the Codex
   SDK). llmtrials strips both down: own system prompt, no tools, no settings or memory, one turn.
   Some context remains:
   - **Codex:** only the user message is sent, plus tool schemas Codex always attaches (~1.6k tokens).
   - **Claude Code:** adds an identity line, your account email, the OS and working directory, the
     model's own name and knowledge cutoff, today's date, and its own thinking/effort defaults
     (observed with Claude Code 2.1.286 on 2026-10-06).

   **Decision (2026-10-06): subscriptions are the main data source**, because per-token API prices are
   too high for this project. The measured condition is therefore *the model inside its vendor's agent
   harness, with tools disabled*. That is close to how an agent built on these products actually runs,
   which fits the research question. To keep it valid:
   - the residue is held constant (fixed working directory, pinned harness versions, explicit
     settings);
   - it is recorded per trial (Claude: the captured request; Codex: a render check per run);
   - `llmtrials status` shows whether it changed during a run;
   - it is reported as part of the condition.

   Comparing with the plain API (OpenRouter) stays optional; see METHODOLOGY for the cost.
2. **Policy and limits.** You may use your own Claude plan with the Agent SDK in your own projects, but
   not offer it to others. Usage counts against the plan's limits: the dedicated monthly Agent SDK
   credit announced for 15 June 2026 was paused
   ([Anthropic support](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)).
   OpenAI documents ChatGPT-login automation with Codex as an advanced option for trusted private
   automation. Plans have 5-hour and weekly limits, which are small for frontier models on ChatGPT
   Plus. llmtrials therefore:
   - pauses automatically and continues after the reset;
   - shows quota in the progress output and in `status`;
   - can keep part of your quota free for yourself (`--quota-cap`).

   Each researcher uses their own accounts.
3. **One answer is not agent behaviour.** A single turn measures the *stated* first action for a
   described state. It does not show error recovery or multi-step drift. Describe tools and state the
   way an agent would see them; a scenario can include a short transcript of earlier steps. Report the
   rest as a limitation.
4. **Outputs vary between calls.** Each scenario × model runs N times (default 5) and is analysed as a
   distribution. Call order is shuffled with a recorded seed.
5. **Sampling cannot be fully controlled.** Subscription routes cannot set `temperature`. Use defaults,
   set the same reasoning effort explicitly for every model (`medium`), and record everything.
6. **Model and harness drift.** Subscription model ids are vendor aliases that can change behind the
   same name. Pin the harness versions (done in `pyproject.toml`), record what each response reports,
   and collect all models within a short window.
7. **Evaluation bias.** Write the rubric before seeing outputs. Rate blind (model identity hidden). Use
   at least two raters on a subset and report agreement. An LLM judge can only be a supplement, checked
   against human ratings.
8. **Forced JSON can change answers,** and models do not always comply (Haiku 4.5 wrapped its JSON in a
   code fence in the first live test). Decide the output format in the protocol, and parse leniently.

## 2. Phases

| Phase | Output | Status |
|---|---|---|
| 0. Base | This repo: runner, routes, example study, docs, tests. Subscription routes verified live 2026-10-06, including quota tracking. | done |
| 1. Topic and protocol | `studies/<topic>/PROTOCOL.md`: question, hypotheses, outcomes, rubric, analysis plan | next |
| 2. Scenarios | Scenarios per category, plus paraphrase variants; peer review; `llmtrials validate` | |
| 3. Pilot | Fast tier (Haiku 4.5, GPT-6 Luna) × 2 repetitions; fix ambiguous scenarios and the rubric; freeze inputs (version 1.0.0) | |
| 4. Collection | One run per model set; let it pause and resume through usage limits; archive run folders | |
| 5. Evaluation | `export --blind`; human rating; inter-rater agreement; optional validated LLM judge | |
| 6. Analysis and report | Per-model distributions with bootstrap CIs over scenarios; publish study + models + runs + analysis | |

## 3. Decisions for you

1. **Topic and research question** (domain, which kinds of decision points).
2. **Output format:** structured JSON (easier to score) or free text (less constraint on the model).
3. **Models:** which tiers from `models/subscriptions.yaml` (frontier / workhorse / fast), and whether to
   add OpenRouter models (open-weight families, or an API comparison).
4. **Size vs. quota:** scenarios × models × repetitions. For example, 30 × 4 × 5 = 600 calls, which will
   take several 5-hour windows on ChatGPT Plus for the frontier models.
5. **Quota to keep for yourself** while studies run (e.g. `--quota-cap 80`).
6. **Licences for sharing:** code (e.g. MIT) and data/scenarios (e.g. CC BY 4.0).
7. **What to publish:** run folders include model answers and the PII-redacted captured requests.

## 4. Immediate next steps

1. Log in once: `llmtrials login-claude`, `llmtrials login-codex`. Then run
   `llmtrials check models/subscriptions.yaml`.
2. Pick the topic. Copy `studies/example` to `studies/<topic>` and write PROTOCOL.md.
3. Draft 5–10 scenarios. Run `llmtrials validate` and review the rendered prompts.
4. Pilot on the fast tier:
   `llmtrials run studies/<topic> --models models/subscriptions.yaml --model claude-haiku-4.5@claude-sub --model gpt-6-luna@chatgpt-sub --repetitions 2 --label pilot`.
5. Read the pilot answers together. Refine scenarios and the rubric, then freeze.
