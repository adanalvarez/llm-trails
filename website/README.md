# website

The interactive dashboard. It is a single static page that reads `data/viz_data.json` at runtime, so
it is fully decoupled from the data: regenerate the dataset and reload, no rebuild needed.

## Run it

Serve the repo root (so both `website/` and `data/` are reachable) and open the site:

```bash
cd ..
python -m http.server 8000
# open http://localhost:8000/website/
```

The page tries `data/viz_data.json` and then `../data/viz_data.json`, so it works whether the site is
served from the repo root or with a `data/` folder sitting next to it.

## Rebuild the page (only if you change layout or copy)

The page is generated from a template so the study's voice and styling stay in one place:

```bash
cd ../experiment
uv run python studies/aws-intrusion/build_dashboard.py   # writes ../website/index.html
```

Styling follows traildiscover.cloud (sky background, navy text, Roboto) for a consistent look across
the two sites.
