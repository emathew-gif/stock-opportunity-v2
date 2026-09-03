# Stock Opportunity of the Week — v2

An independent rebuild of the screener in
[emathew-gif/stock-opportunity](https://github.com/emathew-gif/stock-opportunity).
It runs **only when you click Run workflow** and it **never writes to the v1 repo**.

The v1 holdings spreadsheet and HTML template are fetched from that repo over HTTPS,
read-only, at runtime. Nothing is copied, so this page stays visually identical to v1
even if the template is later restyled.

| | v1 repo | this repo |
|---|---|---|
| Trigger | Monday 06:00 cron | manual only |
| Page | `docs/index.html` | `docs/index.html` (separate site) |
| Archive | none | `data/v2_scores_<date>.csv`, all 150 names |
| Writes to the other repo | — | never |

---

## Setup — three steps, once

### 1. Add the workflow file

**Add file → Create new file**, and type this exact filename:

```
.github/workflows/weekly_v2.yml
```

Paste in:

```yaml
name: Weekly Stock Screener v2

# Manual only — click "Run workflow" in the Actions tab.
# No schedule: this repo runs only when you ask it to.
on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  run-screener-v2:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    env:
      FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run screener v2
        env:
          FINNHUB_API_KEY:   ${{ secrets.FINNHUB_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python screener_v2.py

      - name: Commit and push page and archive
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          mkdir -p /tmp/v2out/data
          cp docs/index.html /tmp/v2out/index.html
          cp -r data/. /tmp/v2out/data/ 2>/dev/null || true
          git fetch origin main
          git reset --hard origin/main
          mkdir -p docs data
          cp /tmp/v2out/index.html docs/index.html
          cp -r /tmp/v2out/data/. data/ 2>/dev/null || true
          git add docs/index.html data
          git diff --staged --quiet || git commit -m "v2 update: $(date +'%Y-%m-%d')"
          git push
```

### 2. Add the two secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `FINNHUB_API_KEY` | same key as the v1 repo |
| `ANTHROPIC_API_KEY` | same key as the v1 repo |

### 3. Turn on Pages

**Settings → Pages → Source: Deploy from a branch → `main` / `/docs`**

Do this *after* the first successful run, since `docs/` does not exist until then.
The page will be at `https://emathew-gif.github.io/stock-opportunity-v2/`.

---

## Running it

**Actions → Weekly Stock Screener v2 → Run workflow.** Takes roughly 15 minutes —
150 tickers at a 0.5 s delay, then ten Claude calls for the write-ups.

---

## What changed from v1

Full detail is in `Methodology_v2_Changes.docx`. In short:

**Structural**

- Cross-sectional **percentile rank** instead of min-max scaling. Min-max was rebuilt
  from each week's own best and worst, so a score of 0.62 in May and 0.62 in August
  did not mean the same thing, and one outlier flattened everyone else.
- Composite is `0.80 × weighted mean + 0.20 × worst sub-score`, so scoring well on
  average no longer excuses failing one lens badly.
- **Cyclical guard** — Value is scaled down when the trailing operating margin runs
  more than 1.5× the five-year average.

**Per factor**

| Factor | Weight | Change |
|---|---|---|
| Value | 25% | Yields not multiples, so loss-makers rank worst rather than cheapest. `pbQuarterly` replaces the stale `pbAnnual`. Sales and EBITDA yields added. |
| Momentum | 10% | 12-1 price return. v1 scored a stock at its 52-week high **zero**, the opposite of the cited Jegadeesh-Titman result. |
| Quality | 30% | Gross profits/assets (Novy-Marx), margin stability, leverage and liquidity added; revenue growth removed. |
| Sentiment | 20% | Three-month consensus **revision**, not level — Womack's result is about changes. The data was already being downloaded and discarded. |
| Catalyst | 15% | Standardised earnings surprise plus post-announcement decay. v1 flagged earnings in the *next* 45 days, which is the opposite of the Post-Earnings Announcement Drift it cited. |

**Archive.** Every run writes all ~150 scored names with every raw metric, all five
sub-scores and their percentiles. v1 kept only the top 10 and overwrote its page each
week, so 140 of 150 rows were discarded. Forward-collected data is free of
survivorship bias by construction, which is what makes the method testable.

---

*Not investment advice. Past performance does not guarantee future results.*
