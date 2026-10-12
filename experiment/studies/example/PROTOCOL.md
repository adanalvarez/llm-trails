# Study protocol: `<study id>`

Fill this in **before collecting data** (pre-registration) and freeze it with the study version.
Log every later change under *Deviations*. Copy this file when you copy the study folder.

## 1. Research question
What exactly do we want to know? One or two sentences.

## 2. Hypotheses
Numbered and falsifiable, each with the measure that tests it.

## 3. Stimuli
- Domain and kind of decision point:
- How scenarios were written or sourced, and by whom:
- Categories and number of scenarios per category:
- Variants (paraphrases, reordered tools...) used to test prompt sensitivity:
- Output format requested from the model, and why:
- Changes made after the pilot:

## 4. Conditions
- Models file(s) and the exact model ids/versions:
- Route per model (API / subscription) and its known harness residue:
- Parameters (reasoning effort, max tokens, host pinning), and why:

## 5. Procedure
- Repetitions per scenario × model:
- Seed:
- Planned collection window (dates):
- Tool versions: recorded automatically in each run's `manifest.json`.

## 6. Outcome measures
What is extracted from each answer (e.g. category of next action, asks a human first, takes an
irreversible action, scope creep), and how: manual coding, parsing, or a judge model.

## 7. Rubric and rating
- Categories with definitions and one example each:
- Raters (who, how many), training, and the share of items double-rated:
- Blinding: `llmtrials export --blind`; raters never see `blind_key.csv`.
- Agreement statistic and target (e.g. Krippendorff's α ≥ 0.8), and how disagreements are resolved:

## 8. Exclusions
Decide now how to treat: provider errors, `tool_calls > 0`, truncated answers
(`finish_reason` = length/max_tokens), unparseable answers, refusals.

## 9. Analysis plan
Primary metrics; comparisons; statistics (e.g. bootstrap CIs over scenarios, mixed-effects models with
scenario as a random effect); correction for multiple comparisons.

## 10. Deviations
| Date | Change | Reason |
|---|---|---|
