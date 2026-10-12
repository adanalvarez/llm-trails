# website

The interactive dashboard: a single static page that reads `../data/viz_data.json` at runtime, so it
stays decoupled from the data. Regenerate the dataset and reload, no rebuild needed.

## Run it locally

Serve the repo root (so `website/` and `data/` are both reachable) and open the site:

```bash
cd ..
python -m http.server 8000
# open http://localhost:8000/website/
```

The page tries `data/viz_data.json` then `../data/viz_data.json`, so it works whether `data/` sits
inside the deployed site (GitHub Pages) or as a sibling at the repo root (local).

## Deploy (GitHub Pages)

`.github/workflows/pages.yml` publishes this folder with `data/` copied in next to `index.html`; set
Pages source to "GitHub Actions". Styling follows traildiscover.cloud for a consistent look.

## Rebuild the page (only when layout, copy, or styling changes)

```bash
cd ../experiment
uv run python studies/aws-intrusion/build_dashboard.py   # writes ../website/index.html
```
