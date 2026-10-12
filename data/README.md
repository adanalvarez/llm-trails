# data

The processed dataset the site reads. Regenerated from raw runs by the experiment scripts, so it is
safe to commit and share (no personal identifiers; every record is scrubbed upstream).

## Files

- `viz_data.json` — everything the dashboard needs, already aggregated and conditioned on each
  situation. Produced by `experiment/studies/aws-intrusion/viz_data.py`.

## Shape of `viz_data.json`

- `n_models`, `n_answers`, `n_events` — study totals.
- `n_freetext`, `n_compliant` — prose answers excluded from the rates, and the compliant denominator.
- `scenarios[]` — per situation: `id`, `label`, `desc`, `target` tactic, `steps`, `n`, `refusal`,
  `target_rate`, a `tactics` map, and `top_events`.
- `model_scenario[]` — per model, the on-offer-tactic rate in each situation (with per-cell `n`).
- `scenario_models{}` — per situation, every model's full tactic breakdown and refusal rate.
- `explorer` — compact (model, situation, event) counts the Event explorer slices client-side:
  `models[]`, `scenarios[]`, `events[]` (with example commands), `denom[][]`, `cells[]`.
- `posture[]` — per model: refuse / report / recon / enact shares.
- `persistence[]`, `first_move[]`, `breadth[]`, `decisions[]` — the model-difference views.
- `names` — the literal roleSessionName / IAM names models used, per-model counts, and the
  clustered patterns (with regexes) worth a detection rule.

## Regenerate

```bash
cd ../experiment
uv run python studies/aws-intrusion/viz_data.py   # writes ../data/viz_data.json
```
