# What LLMs do inside a breached AWS account

Attackers already use LLMs. This project looks at the other side of that: given access to an AWS
account, what does a model reach for next? I put 14 models in 13 post-compromise situations, asked
only for their next move (single-turn, nothing executed), and matched every answer to CloudTrail. The
goal is defensive: turn how models tend to respond into detection priorities.

A companion to [traildiscover.cloud](https://traildiscover.cloud).

## Three parts

| Folder | What's in it |
|---|---|
| [`experiment/`](experiment/) | Run the test yourself: the `llmtrials` harness, the study (prompts, 13 situations, codebook), and the scripts that turn raw runs into the dataset. Start here to reproduce or extend. |
| [`data/`](data/) | The processed dataset the website reads (`viz_data.json`). Regenerated from runs by the experiment scripts. |
| [`website/`](website/) | The interactive dashboard. It reads from `data/` at runtime, so adding new data updates the site with no code change. |

## How it flows

```
experiment/  (run models, code answers, aggregate)
      │  viz_data.py
      ▼
data/viz_data.json
      │  fetched at runtime
      ▼
website/index.html  (the dashboard)
```

Run the study and rebuild the dataset from `experiment/` (see [experiment/README.md](experiment/README.md)).
Drop new data in `data/` and the website reflects it on next load.

## See it

Serve the repo root and open the site:

```bash
python -m http.server 8000
# then open http://localhost:8000/website/
```

## How to read the numbers

Every rate is conditioned on the situation shown. "85% read secrets" means *when secrets are already
located and readable*, not in general. Everything is single-turn: the model states a plan, the harness
records it, nothing runs against a real account.

## License

MIT. See [LICENSE](LICENSE).

---

Research by Claude, reviewed and directed by [Adan Alvarez](https://adan.cloud).
