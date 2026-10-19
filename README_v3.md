# Client Briefs (v3)

`screener_v2.py` ranks 150 names and publishes a list. **v3 is the other half**:
it takes the names *you have chosen* from that list and renders one client-facing
page each.

## One-time setup

`brief_v3.workflow.yml` is in the repo root because the token used to open this
PR has no `workflow` scope and GitHub refuses writes to `.github/workflows/`.
Move it once:

```bash
mkdir -p .github/workflows
git mv brief_v3.workflow.yml .github/workflows/brief_v3.yml
git commit -m "v3: install workflow" && git push
```

Then set two repository variables under Settings → Secrets and variables →
Actions → Variables: `APP_LINK` (may contain `{ticker}`) and `BOOKING_LINK`.
The `FINNHUB_API_KEY` and `ANTHROPIC_API_KEY` secrets already exist for v2.

## Running it

Actions → **Client Briefs v3** → Run workflow → type `NVDA,CVX` → Run.
Leave the box blank and it reads `picks.txt` instead.

Output: `docs/v3/<TICKER>.html`, plus `docs/v3/index.html` listing what was built.
Nothing under `docs/v2.html` or `data/` is touched.

## What is on a page

| Section | Source |
|---|---|
| Which way each number is moving | Finnhub quarterly series, last 6 quarters |
| The case, the risk and what to watch | Claude, from the figures above |
| What you could gain or lose | analyst estimates + the worst real fall in 10 years |
| You may already own it | SPY / QQQ / VUG / SPMO holdings |

## Two things worth knowing

**Sector coverage.** Finnhub's quarterly series is thin for financials — banks
have no gross margin, EBITDA or current ratio. `METRICS_PREFERRED` is tried
first and `METRICS_FALLBACK` (return on equity, return on assets, book value,
debt ratios) tops up. A name that cannot reach `MIN_TILES` is reported and
skipped rather than published half-empty.

**Links.** `APP_LINK` and `BOOKING_LINK` are repository variables
(Settings → Secrets and variables → Actions → Variables). `APP_LINK` may contain
`{ticker}`, e.g. `https://app.example.com/stock/{ticker}`. Unset, the buttons
render as `#`.

## Changing the words

The three answers come from `PROMPT` in `brief_v3.py`. `SYSTEM` holds the
banned-jargon list — EBITDA, capex, hyperscaler, multiple and the rest — and
the rule that a price-to-earnings figure is written as "about $26 for every $1
of yearly profit". Edit those, not the template, to change how the brief reads.
