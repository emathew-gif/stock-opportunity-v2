#!/usr/bin/env python3
"""
Stock Opportunity of the Week — Broad Market Screener v2
=========================================================
Standalone. Lives in its own repo and writes only to its own repo.

  reads   holdings-daily-us-en-spy.xlsx      from the v1 repo over HTTPS, read-only
  reads   stock_opportunity_widget_v2.html   from the v1 repo over HTTPS, read-only
  writes  docs/index.html                    this repo only (its own Pages site)
  writes  data/v2_scores_<date>.csv          this repo only

Nothing in the v1 repo is ever written to. Fetching the template rather than
copying it means this page stays visually identical to v1 if the template is
ever restyled.

Emits the identical DATA shape as v1 so the same template renders it unchanged.

WHAT DIFFERS FROM v1  (see Methodology_v2_Changes.docx)
  structural
    - cross-sectional percentile rank instead of min-max scaling
    - composite = 0.80 * weighted mean + 0.20 * worst sub-score
    - cyclical guard: value score scaled down when TTM margin >> 5y margin
    - stale-reference guard: upside discarded when price sits outside its own
      52-week range (stock-split signature)
  value      yields not multiples (negatives rank worst); pbQuarterly not pbAnnual;
             adds sales yield and EBITDA/EV
  momentum   12-1 price return (was: mid-range 52w position, which penalised winners)
  quality    gross profits/assets, margin stability, leverage, liquidity added;
             revenue growth removed
  sentiment  3-month consensus REVISION (was: consensus level)
  catalyst   standardised earnings surprise + post-announcement decay
             (was: forward earnings-date flag)
"""

import os, json, re, time
import requests
from io import BytesIO
import pandas as pd
import numpy as np
import finnhub
import anthropic
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
FINNHUB_API_KEY   = os.environ["FINNHUB_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Source files live in the v1 repo and are fetched read-only at runtime.
V1_RAW         = "https://raw.githubusercontent.com/emathew-gif/stock-opportunity/main"
HOLDINGS_URL   = f"{V1_RAW}/holdings-daily-us-en-spy.xlsx"
TEMPLATE_URL   = f"{V1_RAW}/stock_opportunity_widget_v2.html"
OUTPUT_HTML    = "docs/index.html"
ARCHIVE_DIR    = "data"
TOP_N_HOLDINGS = 150
TOP_N_OUTPUT   = 10
API_SLEEP      = 0.5

TODAY      = datetime.today().strftime("%Y-%m-%d")
IN_45_DAYS = (datetime.today() + timedelta(days=45)).strftime("%Y-%m-%d")
WEEK_LABEL = f"v2 · Week of {datetime.today().strftime('%d %b %Y')}"

WEIGHTS = {"value": 0.25, "momentum": 0.10, "quality": 0.30,
           "sentiment": 0.20, "catalyst": 0.15}

MIN_PENALTY   = 0.20   # composite = (1-p)*weighted_mean + p*min(sub-scores)
CYC_THRESHOLD = 1.50   # TTM operating margin this many x the 5y average triggers the guard
CYC_FLOOR     = 0.50   # hardest the guard may scale a value score
PEAD_WINDOW   = 60     # days over which post-earnings drift decays to zero

SECTOR_MAP = {
    "NVDA":"Technology","AAPL":"Technology","MSFT":"Technology","AVGO":"Technology",
    "MU":"Technology","AMD":"Technology","LRCX":"Technology","CSCO":"Technology",
    "AMAT":"Technology","INTC":"Technology","PLTR":"Technology","ORCL":"Technology",
    "KLAC":"Technology","IBM":"Technology","TXN":"Technology","APH":"Technology",
    "ADI":"Technology","CRM":"Technology","ANET":"Technology","QCOM":"Technology",
    "GLW":"Technology","PANW":"Technology","ACN":"Technology","VRT":"Technology",
    "STX":"Technology","SNDK":"Technology","WDC":"Technology","INTU":"Technology",
    "ADBE":"Technology","CRWD":"Technology","NOW":"Technology","SNPS":"Technology",
    "APP":"Technology","PWR":"Technology",
    "GOOGL":"Comm Services","GOOG":"Comm Services","META":"Comm Services",
    "NFLX":"Comm Services","DIS":"Comm Services","VZ":"Comm Services",
    "T":"Comm Services","CMCSA":"Comm Services","TMUS":"Comm Services",
    "BKNG":"Comm Services","UBER":"Comm Services",
    "AMZN":"Cons Discretionary","TSLA":"Cons Discretionary","HD":"Cons Discretionary",
    "MCD":"Cons Discretionary","SBUX":"Cons Discretionary","LOW":"Cons Discretionary",
    "TJX":"Cons Discretionary","ORLY":"Cons Discretionary","MAR":"Cons Discretionary",
    "HLT":"Cons Discretionary",
    "WMT":"Cons Staples","COST":"Cons Staples","PG":"Cons Staples",
    "KO":"Cons Staples","PEP":"Cons Staples","PM":"Cons Staples",
    "MO":"Cons Staples","MDLZ":"Cons Staples",
    "XOM":"Energy","CVX":"Energy","COP":"Energy","SLB":"Energy","EOG":"Energy",
    "WMB":"Energy",
    "JPM":"Financials","V":"Financials","MA":"Financials","BAC":"Financials",
    "GS":"Financials","WFC":"Financials","MS":"Financials","AXP":"Financials",
    "SCHW":"Financials","BLK":"Financials","COF":"Financials","CB":"Financials",
    "PGR":"Financials","SPGI":"Financials","CME":"Financials","ICE":"Financials",
    "PNC":"Financials","USB":"Financials","BK":"Financials","BX":"Financials",
    "LLY":"Healthcare","JNJ":"Healthcare","UNH":"Healthcare","ABBV":"Healthcare",
    "MRK":"Healthcare","ABT":"Healthcare","AMGN":"Healthcare","TMO":"Healthcare",
    "GILD":"Healthcare","ISRG":"Healthcare","DHR":"Healthcare","SYK":"Healthcare",
    "BMY":"Healthcare","MDT":"Healthcare","VRTX":"Healthcare","BSX":"Healthcare",
    "PFE":"Healthcare","CVS":"Healthcare","MCK":"Healthcare","REGN":"Healthcare",
    "HCA":"Healthcare",
    "CAT":"Industrials","GE":"Industrials","RTX":"Industrials","HON":"Industrials",
    "UNP":"Industrials","LMT":"Industrials","BA":"Industrials","DE":"Industrials",
    "ETN":"Industrials","PH":"Industrials","GD":"Industrials","CMI":"Industrials",
    "EMR":"Industrials","FDX":"Industrials","CSX":"Industrials","UPS":"Industrials",
    "ADP":"Industrials","GEV":"Industrials","HWM":"Industrials","TT":"Industrials",
    "JCI":"Industrials","WM":"Industrials","NOC":"Industrials","MMM":"Industrials",
    "CRH":"Industrials",
    "LIN":"Materials","NEM":"Materials","FCX":"Materials","SHW":"Materials",
    "WELL":"Real Estate","PLD":"Real Estate","EQIX":"Real Estate","AMT":"Real Estate",
    "NEE":"Utilities","SO":"Utilities","DUK":"Utilities","CEG":"Utilities",
    "AEP":"Utilities",
}

fh     = finnhub.Client(api_key=FINNHUB_API_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Load SPY holdings
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60); print("STEP 1 — Loading SPY holdings"); print("=" * 60)

print(f"Fetching holdings from {HOLDINGS_URL}")
_r = requests.get(HOLDINGS_URL, timeout=60)
_r.raise_for_status()
df_holdings = pd.read_excel(BytesIO(_r.content), sheet_name="holdings", skiprows=4)
df_holdings = df_holdings[["Name", "Ticker", "Weight"]].dropna(subset=["Ticker"])
df_holdings = df_holdings[df_holdings["Ticker"].str.match(r"^[A-Z]{1,5}$")]
df_holdings = (df_holdings.sort_values("Weight", ascending=False)
               .head(TOP_N_HOLDINGS).reset_index(drop=True))
df_holdings["spy_rank"] = df_holdings.index + 1
df_holdings["sector"]   = df_holdings["Ticker"].map(SECTOR_MAP).fillna("Other")
print(f"✓ {len(df_holdings)} holdings across {df_holdings['sector'].nunique()} sectors")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Fetch Finnhub data
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 2 — Fetching Finnhub data"); print("=" * 60)

def safe(fn, *args, **kwargs):
    try:
        r = fn(*args, **kwargs)
        return r if r else None
    except Exception:
        return None

def fetch_ticker(ticker):
    return {
        "quote":    safe(fh.quote, ticker),
        "metrics":  safe(fh.company_basic_financials, ticker, "all"),
        "profile":  safe(fh.company_profile2, symbol=ticker),
        "rec":      safe(fh.recommendation_trends, ticker),
        "target":   safe(fh.price_target, ticker),
        "earnings": safe(fh.earnings_calendar, _from=TODAY, to=IN_45_DAYS, symbol=ticker),
        "surprise": safe(fh.company_earnings, ticker),   # NEW in v2 — PEAD input
    }

tickers = df_holdings["Ticker"].tolist()
print(f"Fetching {len(tickers)} tickers (~{len(tickers)*API_SLEEP/60:.1f} min)...\n")

raw, errors = {}, []
for i, ticker in enumerate(tickers, 1):
    raw[ticker] = fetch_ticker(ticker)
    ok = bool(raw[ticker]["quote"])
    print(f"  [{i:03d}/{len(tickers)}] {ticker:6s} {'✓' if ok else '⚠ no quote'}")
    if not ok:
        errors.append(ticker)
    time.sleep(API_SLEEP)
print(f"\n✓ {len(tickers)-len(errors)} ok  |  {len(errors)} errors: {errors}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Parse
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 3 — Parsing data"); print("=" * 60)

def gm(metrics_resp, key):
    try:
        return metrics_resp["metric"].get(key)
    except Exception:
        return None

def inv(x):
    """Reciprocal, preserving sign. Negative fundamentals stay negative and
    therefore rank WORST — this is what fixes the negative-P/E bug in v1."""
    try:
        x = float(x)
        return 1.0 / x if x != 0 else None
    except Exception:
        return None

def consensus(r):
    if not r:
        return None, None, 0, 0, 0, 0, 0, 0
    sb, b = r.get("strongBuy", 0), r.get("buy", 0)
    h, s  = r.get("hold", 0),      r.get("sell", 0)
    ss    = r.get("strongSell", 0)
    tot   = sb + b + h + s + ss
    if tot == 0:
        return None, None, sb, b, h, s, ss, 0
    score = (sb*1.0 + b*0.75 + h*0.5 + s*0.25 + ss*0.0) / tot
    return score, (sb + b) / tot, sb, b, h, s, ss, tot

def sue_and_decay(surprise_list):
    """Standardised unexpected earnings + how recently it happened.
    SUE = latest surprise / stdev of recent surprises (Bernard-Thomas).
    Drift decays to zero over PEAD_WINDOW days after the announcement."""
    if not surprise_list:
        return None, 0.0, None
    rows = [r for r in surprise_list if r.get("actual") is not None
                                     and r.get("estimate") is not None]
    if not rows:
        return None, 0.0, None
    rows = sorted(rows, key=lambda r: r.get("period", ""), reverse=True)
    surprises = [float(r["actual"]) - float(r["estimate"]) for r in rows[:8]]
    latest    = surprises[0]
    sd        = float(np.std(surprises[1:])) if len(surprises) > 2 else 0.0
    if sd > 0:
        sue = latest / sd
    else:
        sp  = rows[0].get("surprisePercent")
        sue = float(sp) / 100.0 if sp is not None else None
    period = rows[0].get("period")
    decay  = 0.0
    if period:
        try:
            days  = (datetime.today() - datetime.strptime(period, "%Y-%m-%d")).days
            decay = max(0.0, 1.0 - days / PEAD_WINDOW) if days >= 0 else 0.0
        except Exception:
            decay = 0.0
    return sue, decay, period

def parse(ticker):
    d   = raw.get(ticker, {})
    q   = d.get("quote")    or {}
    m   = d.get("metrics")  or {}
    p   = d.get("profile")  or {}
    rec = d.get("rec")      or []
    tgt = d.get("target")   or {}
    ear = d.get("earnings") or {}

    price      = q.get("c")
    prev_close = q.get("pc")
    day_chg    = round((price - prev_close) / prev_close * 100, 2) if price and prev_close else None

    # ── Value inputs — yields, so negatives rank worst ───────────────────────
    pe_ttm   = gm(m, "peTTM") or gm(m, "peBasicExclExtraTTM")
    pb_q     = gm(m, "pbQuarterly")                 # v1 used the STALE pbAnnual
    ps_ttm   = gm(m, "psTTM")
    ev_ebit  = gm(m, "evEbitdaTTM")
    earn_yld, book_yld = inv(pe_ttm), inv(pb_q)
    sales_yld, ebitda_yld = inv(ps_ttm), inv(ev_ebit)

    # ── Quality inputs ───────────────────────────────────────────────────────
    roe        = gm(m, "roeTTM")
    gross_marg = gm(m, "grossMarginTTM")
    asset_turn = gm(m, "assetTurnoverTTM")
    op_ttm     = gm(m, "operatingMarginTTM")
    op_5y      = gm(m, "operatingMargin5Y")
    debt_eq    = gm(m, "totalDebt/totalEquityQuarterly")
    curr_ratio = gm(m, "currentRatioQuarterly")
    gp_assets  = (gross_marg / 100.0) * asset_turn if (gross_marg is not None and asset_turn) else None

    margin_ratio = (op_ttm / op_5y) if (op_ttm and op_5y and op_5y > 0) else None
    margin_stab  = -abs(np.log(margin_ratio)) if (margin_ratio and margin_ratio > 0) else None

    # ── Momentum: 12-1 price return ──────────────────────────────────────────
    r52  = gm(m, "52WeekPriceReturnDaily")
    rmtd = gm(m, "monthToDatePriceReturnDaily")
    mom_12_1 = (r52 - rmtd) if (r52 is not None and rmtd is not None) else r52

    # ── 52-week range, and a data-sanity check on it ─────────────────────────
    w52_high, w52_low = gm(m, "52WeekHigh"), gm(m, "52WeekLow")
    w52_pos = ((price - w52_low) / (w52_high - w52_low)
               if (w52_high and w52_low and price and w52_high != w52_low) else None)

    # If the current price sits OUTSIDE its own 52-week range the reference data
    # is internally inconsistent. The usual cause is a stock split: the quote
    # updates immediately but the 52-week range and the analyst target stay on
    # the pre-split basis, which manufactures a huge fake "upside".
    # Observed 2026-09-04: APH priced at $82.07 against a 52-week low of $108.68
    # and a target of $201.17, producing a fabricated 145% upside on what is
    # really about 23%. Discard the figure rather than publish or score it.
    stale_ref = bool(w52_high and w52_low and price and
                     (price < w52_low * 0.999 or price > w52_high * 1.001))

    # ── Sentiment: level AND 3-month revision ────────────────────────────────
    now_score, buy_ratio, sb, b, h, s, ss, total = consensus(rec[0] if len(rec) > 0 else None)
    old_score = consensus(rec[2])[0] if len(rec) > 2 else None
    rev_3m    = (now_score - old_score) if (now_score is not None and old_score is not None) else None

    mean_target = tgt.get("targetMean")
    upside_pct  = (round((mean_target - price) / price * 100, 1)
                   if (mean_target and price and not stale_ref) else None)
    if stale_ref:
        mean_target = None          # the target is on the same stale basis

    # ── Catalyst: realised surprise, not a forward date ──────────────────────
    sue, pead_decay, last_report = sue_and_decay(d.get("surprise"))

    row    = df_holdings[df_holdings["Ticker"] == ticker]
    sector = row["sector"].values[0] if len(row) > 0 else "Other"

    return {
        "ticker": ticker, "name": p.get("name", ticker), "sector": sector,
        "price": price, "day_chg": day_chg,
        "pe_ttm": pe_ttm, "pb": pb_q, "ps_ttm": ps_ttm, "ev_ebitda": ev_ebit,
        "earn_yld": earn_yld, "book_yld": book_yld,
        "sales_yld": sales_yld, "ebitda_yld": ebitda_yld,
        "roe": roe, "gross_margin": gross_marg, "asset_turnover": asset_turn,
        "gp_assets": gp_assets, "op_margin_ttm": op_ttm, "op_margin_5y": op_5y,
        "margin_ratio": margin_ratio, "margin_stability": margin_stab,
        "debt_equity": debt_eq, "current_ratio": curr_ratio,
        "rev_growth": gm(m, "revenueGrowthTTMYoy"), "eps_growth": gm(m, "epsGrowthTTMYoy"),
        "w52_high": w52_high, "w52_low": w52_low, "w52_pos": w52_pos,
        "stale_ref": stale_ref,
        "mom_12_1": mom_12_1,
        "mean_target": mean_target, "upside_pct": upside_pct,
        "analyst_score": now_score, "analyst_score_3m_ago": old_score,
        "revision_3m": rev_3m, "buy_ratio": buy_ratio,
        "strong_buy": sb, "buy": b, "hold": h, "sell": s, "strong_sell": ss,
        "total_recs": total,
        "sue": sue, "pead_decay": pead_decay, "last_report": last_report,
        "has_earnings": bool(ear.get("earningsCalendar")),
    }

valid = [t for t in tickers if raw.get(t, {}).get("quote")]
df = pd.DataFrame([parse(t) for t in valid])
print(f"✓ Parsed {len(df)} stocks")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Score
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 4 — Scoring (v2)"); print("=" * 60)

def pr(series, invert=False):
    """Cross-sectional percentile rank. Outlier-proof, and comparable week to
    week — unlike v1's min-max scaling, which was rebuilt from each week's own
    extremes. Missing values sit neutral at 0.5."""
    x = pd.to_numeric(series, errors="coerce")
    r = x.rank(pct=True, na_option="keep")
    if invert:
        r = 1.0 - r
    return r.fillna(0.5)

# Value — 30 earnings yield / 25 book yield / 25 sales yield / 20 EBITDA yield
df["score_value_raw"] = (pr(df["earn_yld"])   * 0.30 +
                         pr(df["book_yld"])   * 0.25 +
                         pr(df["sales_yld"])  * 0.25 +
                         pr(df["ebitda_yld"]) * 0.20)

# Cyclical guard — a company earning far above its own 5y norm is not "cheap"
ratio = pd.to_numeric(df["margin_ratio"], errors="coerce")
df["cyc_guard"] = np.where(ratio > CYC_THRESHOLD,
                           np.clip(CYC_THRESHOLD / ratio, CYC_FLOOR, 1.0), 1.0)
df["cyc_guard"] = df["cyc_guard"].fillna(1.0)
df["score_value"] = df["score_value_raw"] * df["cyc_guard"]

# Momentum — 12-1 price return (v1 penalised winners; this rewards them)
df["score_momentum"] = pr(df["mom_12_1"])

# Quality — gross profitability, ROE, margin stability, safety, liquidity
df["score_quality"] = (pr(df["gp_assets"])        * 0.30 +
                       pr(df["roe"])              * 0.25 +
                       pr(df["margin_stability"]) * 0.20 +
                       pr(df["debt_equity"], invert=True) * 0.15 +
                       pr(df["current_ratio"])    * 0.10)

# Sentiment — revision first, level demoted
df["score_sentiment"] = (pr(df["revision_3m"])   * 0.50 +
                         pr(df["upside_pct"])    * 0.30 +
                         pr(df["analyst_score"]) * 0.20)

# Catalyst — realised surprise + drift decay
df["score_catalyst"] = (pr(df["sue"])              * 0.50 +
                        df["pead_decay"].fillna(0) * 0.30 +
                        pr(df["eps_growth"])       * 0.20)

SUB = ["score_value", "score_momentum", "score_quality", "score_sentiment", "score_catalyst"]
df["weighted_mean"] = sum(df[f"score_{k}"] * v for k, v in WEIGHTS.items())
df["worst_lens"]    = df[SUB].min(axis=1)
df["score_composite"] = ((1 - MIN_PENALTY) * df["weighted_mean"]
                         + MIN_PENALTY * df["worst_lens"])

df = df.sort_values("score_composite", ascending=False).reset_index(drop=True)
df["rank"] = df.index + 1
print("✓ Scoring complete")
print(f"\n  cyclical guard triggered on {(df['cyc_guard'] < 1.0).sum()} names")
print(f"  3-month revision available for {df['revision_3m'].notna().sum()} names")
print(f"  earnings surprise available for {df['sue'].notna().sum()} names")
_stale = df["stale_ref"].sum()
if _stale:
    print(f"  ⚠ upside suppressed on {_stale} name(s) with stale reference data "
          f"(price outside its own 52-week range): {df[df['stale_ref']]['ticker'].tolist()}")
print(f"\nTop 10:")
print(df.head(10)[["rank","ticker","sector","score_composite","worst_lens"] + SUB]
        .round(3).to_string(index=False))

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4b — Archive the full cross-section
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 4b — Archiving"); print("=" * 60)
Path(ARCHIVE_DIR).mkdir(exist_ok=True)
arc = df.copy()
for c in SUB + ["score_composite"]:
    arc[c + "_pct"] = arc[c].rank(pct=True)
arc["as_of"], arc["version"], arc["universe"] = TODAY, "v2", f"SPY Top {TOP_N_HOLDINGS}"
arc["in_top_n"] = arc["rank"] <= TOP_N_OUTPUT
archive_path = f"{ARCHIVE_DIR}/v2_scores_{TODAY}.csv"
arc.to_csv(archive_path, index=False)
print(f"✓ {len(arc)} names x {arc.shape[1]} fields -> {archive_path}")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Theses via Claude
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 5 — Generating investment theses"); print("=" * 60)

SYSTEM_PROMPT = """You are a senior equity analyst writing a concise weekly
stock opportunity brief for a retail investment newsletter. Be direct,
specific, and data-driven. Write in plain English — avoid jargon.
Do not use any markdown formatting, asterisks, or bold markers."""

def build_prompt(row):
    def pct(v): return f"{v:.1f}%" if pd.notna(v) else "N/A"
    def x(v):   return f"{v:.1f}x"  if pd.notna(v) else "N/A"
    def d(v):   return f"${v:.2f}"  if pd.notna(v) else "N/A"
    def num(v): return f"{v:.2f}"   if pd.notna(v) else "N/A"
    cyc = ""
    if pd.notna(row["margin_ratio"]) and row["margin_ratio"] > CYC_THRESHOLD:
        cyc = (f"\nNOTE: operating margin is {row['margin_ratio']:.1f}x its 5-year average "
               f"({pct(row['op_margin_ttm'])} vs {pct(row['op_margin_5y'])}) — earnings may be "
               f"at a cyclical peak, so trailing valuation multiples flatter it.")
    if row.get("stale_ref"):
        cyc += ("\nNOTE: analyst target data for this name is stale (likely a recent stock "
                "split), so no upside figure is available. Do not mention price targets.")
    rev = ("improving" if (pd.notna(row["revision_3m"]) and row["revision_3m"] > 0)
           else "deteriorating" if (pd.notna(row["revision_3m"]) and row["revision_3m"] < 0)
           else "unchanged")
    return f"""Stock: {row['ticker']} — {row['name']} ({row['sector']})
Price: {d(row['price'])}  |  P/E (TTM): {x(row['pe_ttm'])}  |  P/B: {x(row['pb'])}  |  P/S: {x(row['ps_ttm'])}
ROE: {pct(row['roe'])}  |  Gross margin: {pct(row['gross_margin'])}  |  Revenue growth YoY: {pct(row['rev_growth'])}
Gross profit per dollar of assets: {num(row['gp_assets'])}
Debt to equity: {num(row['debt_equity'])}  |  Current ratio: {num(row['current_ratio'])}
12-month price return excluding the last month: {pct(row['mom_12_1'])}
Analyst mean target: {d(row['mean_target'])}  |  Upside: {pct(row['upside_pct'])}
Analyst consensus over the last 3 months: {rev}
Consensus: {row['strong_buy']} strong buy / {row['buy']} buy / {row['hold']} hold / {row['sell']} sell
Most recent earnings: {row['last_report'] or 'N/A'}{cyc}

Write exactly 4 labelled sections. Plain English only — no jargon, no markdown.
Never mention score numbers or scoring systems. Reference the actual metrics above
directly using the real numbers (e.g. "13x trailing earnings", "37% upside to analyst
targets", "25 of 30 analysts rate it buy or strong buy"). Write like a human analyst
who has read the data, not a model reporting scores.

THESIS: Why this stock stands out this week. 1-2 sentences.
BULL CASE: The single strongest reason it could do well. 1-2 sentences.
BEAR CASE: 2-3 sentences covering: (1) a company-specific risk such as
execution or earnings disappointment; (2) a valuation risk — the stock may
already price in good news, and if earnings look cyclically elevated say so;
(3) a market risk such as rising interest rates or economic slowdown.
Analyst price targets are estimates and actual results may differ materially.
RISK TAG: Pick exactly one: Speculative | Growth | Value | Quality | Turnaround"""

def generate_thesis(row, max_retries=4):
    backoff = [10, 30, 60, 90]
    for attempt in range(max_retries):
        try:
            msg = claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(row)}])
            return msg.content[0].text.strip()
        except Exception as e:
            wait = backoff[min(attempt, len(backoff)-1)]
            print(f"    {type(e).__name__} attempt {attempt+1}/{max_retries} for {row['ticker']} — waiting {wait}s")
            if attempt < max_retries - 1:
                time.sleep(wait)
    print(f"    FAILED after {max_retries} attempts for {row['ticker']}")
    return ""

def parse_sections(text):
    out = {"thesis": "", "bull": "", "bear": "", "risk_tag": ""}
    if not text:
        return out
    pats = {"thesis":   r"THESIS:?\s*(.*?)(?=BULL CASE|BEAR CASE|RISK TAG|$)",
            "bull":     r"BULL CASE:?\s*(.*?)(?=BEAR CASE|RISK TAG|$)",
            "bear":     r"BEAR CASE:?\s*(.*?)(?=RISK TAG|$)",
            "risk_tag": r"RISK TAG:?\s*(.*?)$"}
    for k, p in pats.items():
        m = re.search(p, text, re.DOTALL | re.IGNORECASE)
        if m:
            out[k] = m.group(1).strip().replace("**", "").strip()
    return out

top_picks = df.head(TOP_N_OUTPUT).copy()
theses = []
for _, row in top_picks.iterrows():
    print(f"\n[{int(row['rank']):02d}] Calling Claude for {row['ticker']}...")
    theses.append(generate_thesis(row))
    time.sleep(5)

top_picks["thesis_raw"] = theses
for k in ["thesis", "bull", "bear", "risk_tag"]:
    top_picks[k] = top_picks["thesis_raw"].apply(lambda r, kk=k: parse_sections(r)[kk])
empty = top_picks["thesis"].eq("").sum()
print(f"\n✓ Theses generated" + (f"  ⚠ {empty} empty" if empty else "  All populated"))

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Build output JSON  (identical shape to v1 so the template is unchanged)
# ─────────────────────────────────────────────────────────────────────────────
print(); print("=" * 60); print("STEP 6 — Building output"); print("=" * 60)

def sf(v, dp=2):
    try:
        f = float(v)
        return None if pd.isna(f) else round(f, dp)
    except Exception:
        return None

top_picks = top_picks.copy()
top_picks["is_featured"] = False
top_picks.iloc[0, top_picks.columns.get_loc("is_featured")] = True

output = {
    "generated_at": TODAY,
    "week_label":   WEEK_LABEL,
    "sector":       "Broad Market",
    "universe":     f"SPY Top {TOP_N_HOLDINGS} · v2",
    "weights":      WEIGHTS,
    "picks":        [],
}

for _, row in top_picks.iterrows():
    output["picks"].append({
        "rank":            int(row["rank"]),
        "ticker":          row["ticker"],
        "name":            row["name"],
        "sector":          row["sector"],
        "is_featured":     bool(row["is_featured"]),
        "price":           sf(row["price"]),
        "day_chg":         sf(row["day_chg"]),
        "pe_ttm":          sf(row["pe_ttm"]),
        "pb":              sf(row["pb"]),
        "roe":             sf(row["roe"]),
        "net_margin":      sf(row["gross_margin"]),
        "rev_growth":      sf(row["rev_growth"]),
        "w52_pos":         sf(row["w52_pos"], 3),
        "w52_high":        sf(row["w52_high"]),
        "w52_low":         sf(row["w52_low"]),
        "mean_target":     sf(row["mean_target"]),
        "upside_pct":      sf(row["upside_pct"], 1),
        "has_earnings":    bool(row["has_earnings"]),
        "risk_tag":        str(row["risk_tag"]).replace("**", "").strip(),
        "thesis":          str(row["thesis"]).replace("**", "").strip(),
        "bull_case":       str(row["bull"]).replace("**", "").strip(),
        "bear_case":       str(row["bear"]).replace("**", "").strip(),
        "score_composite": sf(row["score_composite"], 3),
        "scores": {
            "value":     sf(row["score_value"], 3),
            "momentum":  sf(row["score_momentum"], 3),
            "quality":   sf(row["score_quality"], 3),
            "sentiment": sf(row["score_sentiment"], 3),
            "catalyst":  sf(row["score_catalyst"], 3),
        },
        "analyst": {
            "strong_buy":  int(row["strong_buy"]),
            "buy":         int(row["buy"]),
            "hold":        int(row["hold"]),
            "sell":        int(row["sell"]),
            "strong_sell": int(row["strong_sell"]),
            "total":       int(row["total_recs"]),
        },
    })

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Inject into the fetched template and write docs/index.html
# ─────────────────────────────────────────────────────────────────────────────
Path("docs").mkdir(exist_ok=True)
print(f"Fetching template from {TEMPLATE_URL}")
_t = requests.get(TEMPLATE_URL, timeout=60)
_t.raise_for_status()
html = _t.text

match = re.search(r'const DATA = \{.*?\};', html, re.DOTALL)
if not match:
    raise ValueError("Could not find DATA blob in HTML template — check template file")

html_out = html[:match.start()] + f"const DATA = {json.dumps(output)};" + html[match.end():]
with open(OUTPUT_HTML, "w") as f:
    f.write(html_out)

featured = top_picks.iloc[0]
print(f"✓ Written to {OUTPUT_HTML}")
print(f"  Archive  : {archive_path}  ({len(arc)} names)")
print(f"\n  Week     : {WEEK_LABEL}")
print(f"  Featured : {featured['ticker']} — {featured['name']}")
print(f"  Picks    : {', '.join(top_picks['ticker'].tolist())}")
print(f"\nDone.")
