#!/usr/bin/env python3
"""
brief_v3.py — client brief for hand-picked names
================================================
Runs ALONGSIDE screener_v2.py and touches none of its files.

  reads   picks.txt            one ticker per line (or the TICKERS env var)
  writes  docs/v3/<TICKER>.html
  writes  docs/v3/index.html

screener_v2.py decides which names are worth looking at. This script takes the
ones you have chosen from that list and renders the client-facing brief: a
quarterly direction strip, three plain-English answers, and the money outcomes.

WHY IT IS A SEPARATE SCRIPT
  v2 ranks 150 names and publishes a list. v3 publishes one page per name you
  have decided to put in front of a client. Different cadence, different
  audience, so a separate entry point rather than more flags on v2.

NOTE ON COVERAGE
  Finnhub's quarterly series is thin for financials — banks have no gross
  margin, EBITDA or current ratio. METRICS_PREFERRED is tried first and
  METRICS_FALLBACK fills in; if a name ends up with fewer than MIN_TILES
  usable measures it is reported and skipped rather than published half-empty.
"""

import os, re, sys, json, time, html
from datetime import datetime, timedelta, timezone
from pathlib import Path

import finnhub
import anthropic

FINNHUB_API_KEY   = os.environ["FINNHUB_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
fh     = finnhub.Client(api_key=FINNHUB_API_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

OUT_DIR     = Path("docs/v3")
PICKS_FILE  = "picks.txt"
MODEL       = "claude-haiku-4-5-20251001"
API_SLEEP   = 0.6
MIN_TILES   = 5
QUARTERS    = 6
OVERLAP_ETFS = ["SPY", "QQQ", "VUG", "SPMO"]

# illustration size used in the money section
ACCOUNT, WEIGHT = 50_000, 0.05
POSITION = ACCOUNT * WEIGHT

TODAY      = datetime.now(timezone.utc).strftime("%Y-%m-%d")
IN_90_DAYS = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")

INK, MUT, DIM = "#1c1917", "#57534e", "#a8a29e"
POS, NEG      = "#15803d", "#b91c1c"

# key, label, format, polarity (+1 higher is better), scale, plain-English gloss
METRICS_PREFERRED = [
    ("salesPerShare",     "Sales per share",    "${:.2f}",  1, 1,     "what the company sold, per share"),
    ("ebitda",            "Cash profit",        "${:.0f}bn",1, 1e-3,  "profit before interest, tax and wear-and-tear charges (EBITDA)"),
    ("grossMargin",       "Gross margin",       "{:.0f}%",  1, 100,   "what is left of each sales dollar after making the product"),
    ("operatingMargin",   "Operating margin",   "{:.0f}%",  1, 100,   "what is left after running the business"),
    ("netMargin",         "Net margin",         "{:.0f}%",  1, 100,   "what is left after everything, including tax"),
    ("fcfMargin",         "Free cash flow",     "{:.0f}%",  1, 100,   "cash left over after running and investing in the business"),
    ("totalDebtToEquity", "Debt vs equity",     "{:.2f}",  -1, 1,     "borrowings measured against shareholders&rsquo; money"),
    ("currentRatio",      "Short-term cushion", "{:.2f}x",  1, 1,     "assets available against bills due within a year"),
]
# works for every sector including banks — used to top up when the above are thin
METRICS_FALLBACK = [
    ("roeTTM",                "Return on equity",   "{:.0f}%",  1, 100, "profit earned on shareholders&rsquo; money"),
    ("roaTTM",                "Return on assets",   "{:.1f}%",  1, 100, "profit earned on everything the company owns"),
    ("bookValue",             "Book value",         "${:.0f}bn",1, 1e-3,"what the company would be worth if it sold up and paid its debts"),
    ("totalDebtToTotalAsset", "Debt vs assets",     "{:.2f}",  -1, 1,   "borrowings measured against everything owned"),
    ("pb",                    "Price vs book value","{:.1f}x",  0, 1,   "share price against that book value"),
]


# ── data ─────────────────────────────────────────────────────────────────────
def safe(fn, *a, **k):
    try:
        r = fn(*a, **k)
        return r if r else None
    except Exception as e:
        print(f"      ! {fn.__name__}: {type(e).__name__}")
        return None


def etf_overlap_table():
    """Pulled once for the whole run, not once per ticker."""
    table = {}
    for etf in OVERLAP_ETFS:
        h = safe(fh.etfs_holdings, symbol=etf)
        if not h or not h.get("holdings"):
            print(f"  ! no holdings for {etf}")
            continue
        table[etf] = {
            "as_of":  h.get("atDate") or h.get("as_of_date") or "",
            "weights": {x["symbol"]: x["percent"] for x in h["holdings"] if x.get("symbol")},
        }
        print(f"  {etf:5s} {len(table[etf]['weights']):>4d} holdings  as of {table[etf]['as_of']}")
        time.sleep(API_SLEEP)
    return table


def worst_fall(ticker, years=10):
    """Deepest peak-to-trough on weekly closes, and months to get back."""
    to_ts   = int(datetime.now(timezone.utc).timestamp())
    from_ts = to_ts - years * 365 * 24 * 3600
    c = safe(fh.stock_candles, ticker, "W", from_ts, to_ts)
    if not c or c.get("s") != "ok" or len(c.get("c", [])) < 52:
        return None
    px, ts = c["c"], c["t"]
    peak, peak_i = px[0], 0
    worst = {"pct": 0.0}
    for i, p in enumerate(px):
        if p > peak:
            peak, peak_i = p, i
        dd = p / peak - 1
        if dd < worst["pct"]:
            worst = {"pct": dd, "peak": peak, "peak_i": peak_i, "trough": p, "trough_i": i}
    if not worst.get("peak"):
        return None
    rec_i = next((j for j in range(worst["trough_i"], len(px)) if px[j] >= worst["peak"]), None)
    months = round((ts[rec_i] - ts[worst["trough_i"]]) / (30.44 * 86400)) if rec_i else None
    return {
        "pct":        worst["pct"] * 100,
        "peak":       worst["peak"],
        "trough":     worst["trough"],
        "peak_date":  datetime.utcfromtimestamp(ts[worst["peak_i"]]).strftime("%b %Y"),
        "trough_date":datetime.utcfromtimestamp(ts[worst["trough_i"]]).strftime("%b %Y"),
        "recovery_months": months,
    }


def pick_metrics(series):
    """Preferred eight, topped up from the universal set when a sector is thin."""
    chosen, seen = [], set()
    for pool in (METRICS_PREFERRED, METRICS_FALLBACK):
        for key, label, fmt, pol, mult, gloss in pool:
            if key in seen:
                continue
            pts = sorted(series.get(key, []), key=lambda x: x["period"])[-QUARTERS:]
            if len(pts) < 4:
                continue
            chosen.append({"key": key, "label": label, "fmt": fmt, "pol": pol,
                           "mult": mult, "gloss": gloss,
                           "vals": [p["v"] for p in pts],
                           "last_period": pts[-1]["period"]})
            seen.add(key)
            if len(chosen) == 8:
                return chosen
    return chosen


# ── the words ────────────────────────────────────────────────────────────────
SYSTEM = """You write client briefs for a wealth manager whose readers are often
beginners. Plain English only. Never use these words: EBITDA, capex,
hyperscaler, multiple, valuation multiple, percentile, consensus, drawdown,
peak-to-trough, secular, headwind, tailwind, moat. Never write a P/E as "26x" —
write it as "about $26 for every $1 of yearly profit". Short sentences. No
markdown, no asterisks. Never recommend buying or selling, never say a stock is
cheap, attractive or a good opportunity, and never predict a price."""

PROMPT = """{ticker} — {name} ({industry})
Share price {price}. {analysts}
Profit kept per sales dollar: {op_margin} against about {sector_hint} for a typical
company in its industry.
Price against last year's profit: about ${pe} for every $1.
Revenue growth over the past year: {rev_growth}.
Next results: {next_earnings}.
Worst fall in the last ten years: {worst_fall}.
Recent quarterly direction: {directions}.

Write exactly five labelled blocks. Plain English, no jargon, no markdown.

DESCRIPTION: One sentence, max 28 words, saying what this company actually makes
or does, as you would explain it to someone who has never heard of it.

GROWTH: Three bullets, each starting with a two-or-three word bold lead-in
followed by a full stop, then one or two short sentences. Reasons the business
could keep growing. Use the real numbers above where they help.
GROWTH_CLOSE: One short sentence on what would have to hold for those to work.

RISKS: Three bullets in the same shape. The things most likely to go wrong.
One of them must be about what the share price already assumes, and must
explain the consequence in plain terms rather than just stating a ratio.
RISKS_CLOSE: One short sentence naming which risk matters most.

WATCH: Three bullets in the same shape. Specific, checkable things — dated
events, named competitors, a figure that would change the picture.
WATCH_CLOSE: One short sentence saying you would revisit if those change, and
that share price movement alone would not be a reason.

Write each bullet as: LEAD|text   (the pipe separates the bold lead-in)"""


def write_words(ctx, attempts=3):
    """Ask until we get three bullets in each block. A short brief is a bug, not a style."""
    backoff = [0, 8, 20]
    last = ""
    for i in range(attempts):
        if backoff[min(i, len(backoff) - 1)]:
            time.sleep(backoff[min(i, len(backoff) - 1)])
        try:
            msg = claude.messages.create(
                model=MODEL, max_tokens=1200, system=SYSTEM,
                messages=[{"role": "user", "content": PROMPT.format(**ctx)}])
            last = msg.content[0].text.strip()
        except Exception as e:
            print(f"      ! model attempt {i+1}: {type(e).__name__}")
            continue
        parsed = parse_words(last)
        if all(len(parsed[k]) == 3 for k in ("growth", "risks", "watch")) and parsed["description"]:
            return parsed
        print(f"      ! model attempt {i+1}: "
              f"{[len(parsed[k]) for k in ('growth','risks','watch')]} bullets, wanted 3/3/3")
    return parse_words(last)


def parse_words(text):
    out = {"description": "", "growth": [], "growth_close": "",
           "risks": [], "risks_close": "", "watch": [], "watch_close": ""}
    blocks = {
        "description":  r"DESCRIPTION:?\s*(.*?)(?=GROWTH:|$)",
        "growth":       r"GROWTH:?\s*(.*?)(?=GROWTH_CLOSE:|$)",
        "growth_close": r"GROWTH_CLOSE:?\s*(.*?)(?=RISKS:|$)",
        "risks":        r"RISKS:?\s*(.*?)(?=RISKS_CLOSE:|$)",
        "risks_close":  r"RISKS_CLOSE:?\s*(.*?)(?=WATCH:|$)",
        "watch":        r"WATCH:?\s*(.*?)(?=WATCH_CLOSE:|$)",
        "watch_close":  r"WATCH_CLOSE:?\s*(.*?)$",
    }
    for k, pat in blocks.items():
        m = re.search(pat, text, re.S | re.I)
        if not m:
            continue
        body = m.group(1).strip().replace("**", "")
        if k in ("growth", "risks", "watch"):
            lines = [l.strip().lstrip("-\u2022* ").strip() for l in body.splitlines()]
            lines = [l for l in lines if l and "|" in l]
            for line in lines:
                lead, rest = line.split("|", 1)
                out[k].append((lead.strip().rstrip(".") + ".", rest.strip()))
        else:
            out[k] = " ".join(body.split())
    return out


# ── rendering ────────────────────────────────────────────────────────────────
def esc(s):
    return html.escape(str(s), quote=False)


def spark(vals, colour, w=58, h=18):
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = [(i * (w / (len(vals) - 1)), h - 2 - ((v - lo) / rng) * (h - 4)) for i, v in enumerate(vals)]
    d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round" opacity="0.55"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.1" fill="{colour}"/></svg>')


def render_tiles(metrics):
    tiles, gloss, wrong = [], [], 0
    for m in metrics:
        v = m["vals"]
        chg = v[-1] - v[-2]
        pct = chg / abs(v[-2]) * 100 if v[-2] else 0
        flat = abs(pct) < 1.5
        if m["pol"] == 0:          # valuation measures carry no good/bad colour
            col, good = DIM, None
        elif flat:
            col, good = DIM, None
        else:
            good = (chg > 0) if m["pol"] > 0 else (chg < 0)
            col = POS if good else NEG
        if good is False:
            wrong += 1
        arrow = "&#9644;" if flat else ("&#9650;" if chg > 0 else "&#9660;")
        txt = "unchanged" if flat else f"{abs(pct):.0f}%"
        tiles.append(
            f'        <div class="mt">\n'
            f'          <div class="mt-k">{m["label"]}</div>\n'
            f'          <div class="mt-r"><span class="mt-v num">{m["fmt"].format(v[-1]*m["mult"])}</span>\n'
            f'            <span class="mt-d" style="color:{col};">{arrow} {txt}</span>\n'
            f'            <span class="mt-s">{spark(v, col)}</span></div>\n'
            f'        </div>')
        gloss.append(f'<b>{m["label"]}</b> &mdash; {m["gloss"]}')
    return "\n".join(tiles), " &middot; ".join(gloss), wrong


def bullets(items):
    return "\n".join(f'        <li><b>{esc(lead)}</b> {esc(rest)}</li>' for lead, rest in items)


# ── page template (CSS lifted verbatim from the approved NVDA brief) ─────────
CSS = """  :root {
    --bg:#fafaf9; --card:#ffffff; --card-2:#f5f5f4; --border:#e7e5e4;
    --text:#1c1917; --muted:#57534e; --dim:#a8a29e;
    --pos:#15803d; --neg:#b91c1c; --warn:#b45309; --info:#1e3a5f; --accent:#7c2d12;
    --shadow: 0 1px 2px rgba(28,25,23,0.04), 0 1px 3px rgba(28,25,23,0.03);
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif;
    -webkit-font-smoothing:antialiased; padding:32px 24px 56px; }
  .num { font-family:"SF Mono","JetBrains Mono",ui-monospace,Menlo,monospace; font-variant-numeric:tabular-nums; }
  .wrap { max-width:940px; margin:0 auto; }

  .head { display:flex; align-items:flex-end; justify-content:space-between; gap:24px;
          padding-bottom:18px; border-bottom:1px solid var(--border); margin-bottom:26px; flex-wrap:wrap; }
  .head h1 { font-size:26px; font-weight:600; letter-spacing:-0.01em; color:var(--info); }
  .head .tag { display:inline-block; background:var(--accent); color:#fff; font-size:11px;
          padding:3px 8px; border-radius:4px; margin-left:6px; text-transform:uppercase;
          letter-spacing:0.04em; vertical-align:middle; font-weight:500; }
  .head .sub { color:var(--muted); margin-top:5px; font-size:13px; max-width:66ch; }
  .head .px { font-size:28px; font-weight:600; text-align:right; }
  .head .asof { color:var(--dim); font-size:11px; margin-top:3px; text-align:right; }

  .lbl { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.08em;
         font-weight:600; margin-bottom:12px; }

  /* ── The summary ── */
  .sum { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; margin-bottom:16px; }
  @media (max-width:880px) { .sum { grid-template-columns:1fr; gap:14px; } }
  .q { background:var(--card); border:1px solid var(--border); border-radius:8px;
       padding:18px 20px 16px; box-shadow:var(--shadow); border-top:3px solid var(--info); }
  .q h2 { font-size:15px; font-weight:600; color:var(--info); line-height:1.4; margin-bottom:10px; }
  .q ul { list-style:none; }
  .q li { font-size:13px; line-height:1.6; color:var(--text); padding-left:14px;
          position:relative; margin-bottom:8px; }
  .q li::before { content:""; position:absolute; left:0; top:8px; width:5px; height:5px;
          border-radius:50%; background:var(--info); opacity:0.5; }
  .q li b { color:var(--info); font-weight:600; }
  .q .close { font-size:12px; line-height:1.6; color:var(--muted); margin-top:11px;
          padding-top:10px; border-top:1px solid var(--border); }
  .readtime { color:var(--dim); font-size:11px; margin-bottom:30px; }

  /* ── Gains and losses ── */
  .pay { background:var(--card); border:1px solid var(--border); border-radius:8px;
         padding:18px 22px 18px; box-shadow:var(--shadow); margin-bottom:34px; }
  .pay .intro { font-size:13px; line-height:1.65; color:var(--text); margin-bottom:14px; max-width:78ch; }
  .pay-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  @media (max-width:760px) { .pay-grid { grid-template-columns:1fr; } }
  .sc { background:var(--card-2); border:1px solid var(--border); border-radius:6px; padding:14px 15px; }
  .sc.up { border-left:3px solid var(--pos); } .sc.mid { border-left:3px solid var(--dim); }
  .sc.down { border-left:3px solid var(--neg); }
  .sc .t { font-size:13px; font-weight:600; color:var(--text); }
  .sc .m { font-size:21px; font-weight:600; margin-top:7px; }
  .sc .d { font-size:14px; font-weight:600; margin-top:2px; }
  .sc.up .m, .sc.up .d { color:var(--pos); }
  .sc.down .m, .sc.down .d { color:var(--neg); }
  .sc.mid .m, .sc.mid .d { color:var(--muted); }
  .sc .w { font-size:11.5px; line-height:1.55; color:var(--muted); margin-top:8px; }
  .pay .limits { font-size:12px; line-height:1.65; color:var(--muted); margin-top:14px;
         padding-top:12px; border-top:1px solid var(--border); max-width:84ch; }
  .pay .limits b { color:var(--text); font-weight:600; }

  /* ── Deep dive ── */
  .panel { background:var(--card); border:1px solid var(--border); border-radius:8px;
           padding:16px 20px 18px; margin-bottom:16px; box-shadow:var(--shadow); }
  .cadence { color:var(--dim); font-size:11px; margin-bottom:12px; }
  .mt-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px 22px; }
  @media (max-width:820px) { .mt-grid { grid-template-columns:repeat(2,1fr); } }
  @media (max-width:470px) { .mt-grid { grid-template-columns:1fr; } }
  .mt-k { color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:0.06em; font-weight:600; }
  .mt-r { display:flex; align-items:center; gap:8px; margin-top:5px; flex-wrap:wrap; }
  .mt-v { font-size:17px; font-weight:600; color:var(--text); }
  .mt-d { font-size:11px; font-weight:600; white-space:nowrap; }
  .mt-s { margin-left:auto; line-height:0; }
  .foot { color:var(--dim); font-size:11px; margin-top:14px; padding-top:11px;
          border-top:1px solid var(--border); line-height:1.65; }
  .foot b { color:var(--muted); font-weight:600; }

  /* ── The nudge ── */
  .app { background:var(--info); border-radius:8px; padding:22px 26px; box-shadow:var(--shadow); margin:34px 0 28px; }
  .app h3 { color:#fff; font-size:15px; font-weight:600; margin-bottom:9px; }
  .app p { color:rgba(255,255,255,0.86); font-size:13.5px; line-height:1.65; max-width:64ch; }
  .app ul { margin:12px 0 0; padding-left:18px; }
  .app li { color:rgba(255,255,255,0.86); font-size:13px; line-height:1.7; }
  .app .act { display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }
  .app a { display:inline-block; text-decoration:none; font-size:13px; font-weight:600;
           padding:10px 18px; border-radius:6px; }
  .app a.primary { background:#fff; color:var(--info); }
  .app a.ghost { border:1px solid rgba(255,255,255,0.45); color:#fff; }
  .app a:focus-visible { outline:2px solid #fff; outline-offset:2px; }

  .edu { padding:10px 16px; background:var(--card); border:1px solid var(--border);
         border-radius:6px; color:var(--muted); font-size:11px; text-align:center; }
  .prov { display:none; margin-top:14px; padding-top:14px; border-top:1px solid var(--border);
          color:var(--dim); font-size:11px; line-height:1.6; }
  .prov span { color:var(--muted); font-weight:500; }

  @page { size:A4 portrait; margin:14mm 13mm; }
  @media print {
    html, body { background:#fff; }
    body { padding:0; font-size:10.5px; }
    * { -webkit-print-color-adjust:exact; print-color-adjust:exact; box-shadow:none !important; }
    .wrap { max-width:none; }
    .q, .pay, .panel, .app { break-inside:avoid; page-break-inside:avoid; }
    .prov { display:block !important; }
    .app .act { display:none; }
  }"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{ticker} — what it means for investors</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">

  <div class="head">
    <div>
      <h1>{ticker} <span class="tag">{industry}</span></h1>
      <div class="sub">{name} &middot; {description}</div>
    </div>
    <div>
      <div class="px num">${price:,.2f}</div>
      <div class="asof">{price_date}</div>
    </div>
  </div>

  <div class="panel">
    <div class="lbl" style="margin-bottom:4px;">Which way each number is moving</div>
    <div class="cadence">Quarterly &middot; {n_quarters} quarters to {last_quarter}</div>
    <div class="mt-grid">
{tiles}
    </div>
    <div class="foot">{gloss}</div>
    <div class="foot" style="border-top:none; padding-top:4px; margin-top:4px;">Each arrow is the
      change from the quarter before. Green means the number moved in the direction that is usually
      good for that measure, red the opposite &mdash; more debt is red, a bigger cushion is green.
      These are results the company has already reported, not forecasts.{wrong_note}</div>
  </div>

  <div class="lbl">The case, the risk and what to watch</div>
  <div class="sum">

    <div class="q">
      <h2>Why could {short_name} keep growing?</h2>
      <ul>
{growth}
      </ul>
      <div class="close">{growth_close}</div>
    </div>

    <div class="q">
      <h2>What are the main risks?</h2>
      <ul>
{risks}
      </ul>
      <div class="close">{risks_close}</div>
    </div>

    <div class="q">
      <h2>What should investors watch next?</h2>
      <ul>
{watch}
      </ul>
      <div class="close">{watch_close}</div>
    </div>

  </div>
  <div class="readtime">About thirty seconds. Everything below is detail you can take or leave.</div>

  <div class="lbl">What you could gain or lose</div>
  <div class="pay">
    <div class="intro">If you put <b>${position:,.0f}</b> into {short_name} &mdash; 5% of a
      ${account:,.0f} portfolio &mdash; here is what three different outcomes would be worth to you
      in money rather than percentages.</div>
    <div class="pay-grid">
{scenarios}
    </div>
    <div class="limits"><b>What these numbers cannot tell you.</b> None of them is a probability,
      and we are not saying one is more likely than another. The first two come from analyst
      estimates, which are opinions and are often wrong in both directions. The third is the one
      thing here that actually happened. The ${position:,.0f} is an illustration, not a recommended
      amount for you &mdash; your own figure would change every number above.</div>
  </div>

{overlap}

  <div class="app">
    <h3>The numbers live in the app</h3>
    <p>This note does not repeat them. The price, the figures above, analyst estimates, the chart
    and the results calendar are all in the app and always current, which a document never is.</p>
    <ul>
      <li>Pull up {ticker} for the live price and analyst estimates</li>
      <li>Check your own holdings for what you already own through funds</li>
      <li>{watch_cta}</li>
    </ul>
    <div class="act">
      <a class="primary" href="{app_link}">Open {ticker} in the app</a>
      <a class="ghost" href="{booking_link}">Talk to us about sizing</a>
    </div>
  </div>

  <div class="edu">Educational content prepared by CUSP Wealth. Not personalised investment advice,
    and not a recommendation to buy or sell any security. Past performance does not guarantee
    future results.</div>

  <div class="prov">
    <span>Live data:</span> Finnhub &mdash; quote, analyst estimates, results dates &middot;
    <span>Quarterly series:</span> Finnhub basic financials, {n_quarters} quarters to {last_quarter} &middot;
    <span>Computed from weekly closes:</span> worst fall and recovery time, ten years &middot;
    <span>ETF weights:</span> fund disclosures via Finnhub &middot;
    <span>Advisor commentary:</span> the three summary answers &middot;
    Generated {generated} &middot; prices as of {price_date}.
  </div>

</div>
</body>
</html>
"""

SCENARIO = """      <div class="sc {cls}">
        <div class="t">{title}</div>
        <div class="m num">{pct}</div><div class="d num">{money}</div>
        <div class="w">{note}</div>
      </div>"""

OVERLAP_BLOCK = """  <div class="lbl">You may already own it</div>
  <div class="panel">
    <div class="pv" style="font-size:13.5px; line-height:1.65;">{line}</div>
    <div class="foot">ETF weights from fund disclosures via Finnhub. Funds report on different
      schedules, so the dates above are not all the same day.</div>
  </div>
"""


def money(pct, pos):
    v = POSITION * pct / 100
    s = f"${abs(v):,.0f}"
    return ("+" if v >= 0 else "&minus;") + s


def build(ticker, etfs, app_link, booking_link):
    print(f"\n[{ticker}]")
    q    = safe(fh.quote, ticker)
    prof = safe(fh.company_profile2, symbol=ticker)
    bf   = safe(fh.company_basic_financials, ticker, "all")
    tgt  = safe(fh.price_target, ticker)
    cal  = safe(fh.earnings_calendar, _from=TODAY, to=IN_90_DAYS, symbol=ticker)
    if not q or not bf or not prof:
        print("  ! missing core data — skipped")
        return None

    metric = bf.get("metric", {}) or {}
    series = (bf.get("series", {}) or {}).get("quarterly", {}) or {}
    metrics = pick_metrics(series)
    if len(metrics) < MIN_TILES:
        print(f"  ! only {len(metrics)} usable measures (need {MIN_TILES}) — skipped. "
              f"Finnhub's quarterly series is thin for this sector.")
        return None
    print(f"  {len(metrics)} measures: {', '.join(m['label'] for m in metrics)}")

    price = q.get("c") or 0
    fall  = worst_fall(ticker)
    if not fall:
        print("  ! no usable price history — skipped")
        return None
    print(f"  worst fall {fall['pct']:.1f}% ({fall['peak_date']} to {fall['trough_date']})")

    mean_t = (tgt or {}).get("targetMean")
    low_t  = (tgt or {}).get("targetLow")
    n_an   = (tgt or {}).get("numberAnalysts")
    pe     = metric.get("peTTM") or metric.get("peBasicExclExtraTTM")
    opm    = metric.get("operatingMarginTTM")
    revg   = metric.get("revenueGrowthTTMYoy")
    nxt    = (cal or {}).get("earningsCalendar") or []
    nxt_date = nxt[0]["date"] if nxt else None

    directions = ", ".join(
        f"{m['label']} {'up' if m['vals'][-1] > m['vals'][-2] else 'down'}" for m in metrics[:6])

    words = write_words({
        "ticker": ticker, "name": prof.get("name", ticker),
        "industry": prof.get("finnhubIndustry", ""),
        "price": f"${price:,.2f}",
        "analysts": (f"{n_an} analysts put the average twelve-month estimate at ${mean_t:,.2f}, "
                     f"the most cautious at ${low_t:,.2f}." if mean_t and low_t else
                     "No analyst estimates available."),
        "op_margin": f"{opm:.0f} cents" if opm else "not reported",
        "sector_hint": "27 cents",
        "pe": f"{pe:.0f}" if pe else "not meaningful",
        "rev_growth": f"{revg:.0f}%" if revg else "not reported",
        "next_earnings": nxt_date or "not scheduled yet",
        "worst_fall": f"{fall['pct']:.0f}% between {fall['peak_date']} and {fall['trough_date']}",
        "directions": directions,
    })
    if any(len(words[k]) != 3 for k in ("growth", "risks", "watch")):
        print("  ! model output incomplete after retries — skipped")
        return None

    scen = []
    if mean_t:
        up = (mean_t / price - 1) * 100
        scen.append(SCENARIO.format(cls="up", title="If analysts are right",
            pct=f"+{up:.0f}%", money=money(up, True),
            note=f"The average of {n_an or 'several'} analysts&rsquo; twelve-month price estimates."))
    if low_t:
        dn = (low_t / price - 1) * 100
        scen.append(SCENARIO.format(cls="mid", title="If the most cautious is right",
            pct=f"{'&minus;' if dn < 0 else '+'}{abs(dn):.0f}%", money=money(dn, False),
            note="The lowest estimate any of them published."))
    scen.append(SCENARIO.format(cls="down",
        title=f"If {fall['trough_date'][-4:]} happens again",
        pct=f"&minus;{abs(fall['pct']):.0f}%", money=money(fall["pct"], False),
        note="Not a forecast &mdash; what the shares actually did last time."))

    held = [(e, t["weights"][ticker]) for e, t in etfs.items() if ticker in t["weights"]]
    missing = [e for e in etfs if ticker not in etfs[e]["weights"]]
    if held:
        line = ("{} is ".format(prof.get("name", ticker))
                + ", ".join(f"<b>{w:.1f}%</b> of {e}" for e, w in held))
        if missing:
            line += f" &mdash; and <b>not held at all</b> in {', '.join(missing)}"
        line += (". If you hold any of those funds you already own some; check in the app before "
                 "adding more.")
    else:
        line = (f"{prof.get('name', ticker)} is not among the holdings of "
                f"{', '.join(etfs)}, so a purchase here is new exposure rather than more of "
                f"something you already own.")

    tiles, gloss, wrong = render_tiles(metrics)
    wrong_note = (f" <b>{['None','One','Two','Three','Four','Five','Six','Seven','Eight'][wrong]} "
                  f"{'is' if wrong == 1 else 'are'} moving the wrong way</b>, which is why they are "
                  f"shown as arrows rather than a list of figures." if wrong else "")

    page = PAGE.format(
        css=CSS, ticker=ticker, name=esc(prof.get("name", ticker)),
        short_name=esc(prof.get("name", ticker).split()[0]),
        industry=esc(prof.get("finnhubIndustry", "")),
        description=esc(words["description"]),
        price=price, price_date=datetime.now(timezone.utc).strftime("%d %b %Y"),
        n_quarters=len(metrics[0]["vals"]), last_quarter=metrics[0]["last_period"][:7],
        tiles=tiles, gloss=gloss, wrong_note=wrong_note,
        growth=bullets(words["growth"]), growth_close=esc(words["growth_close"]),
        risks=bullets(words["risks"]),  risks_close=esc(words["risks_close"]),
        watch=bullets(words["watch"]),  watch_close=esc(words["watch_close"]),
        position=POSITION, account=ACCOUNT, scenarios="\n".join(scen),
        overlap=OVERLAP_BLOCK.format(line=line),
        watch_cta=(f"Set an alert for the {nxt_date} results" if nxt_date
                   else "Set an alert for the next results"),
        app_link=app_link.format(ticker=ticker), booking_link=booking_link,
        generated=TODAY)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{ticker}.html").write_text(page, encoding="utf-8")
    print(f"  -> {OUT_DIR / (ticker + '.html')}")
    return {"ticker": ticker, "name": prof.get("name", ticker),
            "industry": prof.get("finnhubIndustry", ""), "price": price}


INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Client briefs</title><style>{css}</style></head>
<body><div class="wrap">
  <div class="head"><div><h1>Client briefs</h1>
    <div class="sub">Names selected from the weekly screen and written up for clients.
      Generated {today}.</div></div></div>
  <div class="panel">{rows}</div>
  <div class="edu">Educational content prepared by CUSP Wealth. Not personalised investment
    advice, and not a recommendation to buy or sell any security.</div>
</div></body></html>"""


def main():
    env = os.environ.get("TICKERS", "").strip()
    if env:
        tickers = [t.strip().upper() for t in re.split(r"[,\s]+", env) if t.strip()]
    elif Path(PICKS_FILE).exists():
        tickers = [l.strip().upper() for l in Path(PICKS_FILE).read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
    else:
        print(f"No TICKERS env var and no {PICKS_FILE}. Nothing to do.")
        return 1

    app_link     = os.environ.get("APP_LINK", "#")       # e.g. https://app.example.com/stock/{ticker}
    booking_link = os.environ.get("BOOKING_LINK", "#")

    print("=" * 60); print(f"brief_v3 — {len(tickers)} name(s): {', '.join(tickers)}"); print("=" * 60)
    print("\nETF holdings (pulled once for the whole run)")
    etfs = etf_overlap_table()

    built, skipped = [], []
    for t in tickers:
        try:
            r = build(t, etfs, app_link, booking_link)
            (built if r else skipped).append(r or t)
        except Exception as e:
            print(f"  ! {type(e).__name__}: {e}")
            skipped.append(t)
        time.sleep(API_SLEEP)

    if built:
        rows = "\n".join(
            f'<div class="prow" style="display:grid;grid-template-columns:90px 1fr 110px;gap:16px;'
            f'align-items:baseline;padding:12px 0;border-bottom:1px solid var(--border);">'
            f'<a href="{b["ticker"]}.html" style="font-weight:600;color:var(--info);'
            f'text-decoration:none;">{b["ticker"]}</a>'
            f'<span style="font-size:13px;">{esc(b["name"])}<span style="color:var(--dim);">'
            f' &middot; {esc(b["industry"])}</span></span>'
            f'<span class="num" style="text-align:right;">${b["price"]:,.2f}</span></div>'
            for b in built)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "index.html").write_text(
            INDEX.format(css=CSS, today=TODAY, rows=rows), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"built {len(built)}: {', '.join(b['ticker'] for b in built) or '—'}")
    if skipped:
        print(f"skipped {len(skipped)}: {', '.join(skipped)}")
    print("=" * 60)
    return 0 if built else 1


if __name__ == "__main__":
    sys.exit(main())
