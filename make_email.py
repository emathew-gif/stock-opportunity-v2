#!/usr/bin/env python3
"""
Client emailer — runs AFTER screener_v2.py, changes no scoring.

Writes docs/email.html: a self-contained, email-safe rendering of the week's
featured pick. Separate file from the web pages, which are unchanged.

WHY A SEPARATE FILE
The web pages are a JavaScript app -- renderHero(), renderTable(), a DATA blob.
Every mail client strips JavaScript, so the pages cannot be emailed at all. This
output is built to survive Outlook, Gmail and Apple Mail:
  - no JavaScript, no <style> block, no CSS variables, no flexbox
  - table layout with inline styles only
  - 600px fixed width
  - bars drawn from table cells with bgcolor, so they render with images off
"""
import json, re, glob, csv, os, html

PAGE   = "docs/index.html"
OUT    = "docs/email.html"

INK, INK2, INK3 = "#0a0a0a", "#3a3a3a", "#7a7a7a"
PAPER, PAPER2, RULE = "#f5f2eb", "#ede9e0", "#ded8cc"
GREEN, RED, ACCENT  = "#1a6b3a", "#b52525", "#c8410a"

BUCKETS = [
    ("value",     "Value",     "Earnings, book, sales and EBITDA yields vs sector", "25%"),
    ("momentum",  "Momentum",  "12-month price trend, excluding the most recent month", "10%"),
    ("quality",   "Quality",   "Profitability, margin stability, leverage and liquidity", "30%"),
    ("sentiment", "Sentiment", "Direction of analyst revisions over three months", "20%"),
    ("catalyst",  "Catalyst",  "Size and recency of the last earnings surprise", "15%"),
]

def latest_archive():
    f = sorted(glob.glob("data/v2_scores_*.csv"))
    return f[-1] if f else None

def load_universe():
    rows, path = [], latest_archive()
    if not path:
        return rows
    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    return rows

def fnum(v, dp=2, suffix="", dash="—"):
    try:
        return f"{float(v):,.{dp}f}{suffix}"
    except (TypeError, ValueError):
        return dash

def band(pct):
    """Plain-English position, from a 0-1 percentile."""
    if pct is None:      return "—"
    p = pct * 100
    if p >= 90: return "Top 10%"
    if p >= 80: return "Top 20%"
    if p >= 70: return "Top 30%"
    if p >= 60: return "Top 40%"
    if p >= 40: return "Middle of the pack"
    if p >= 20: return "Bottom 40%"
    return "Bottom 20%"

def bar(score, filled_colour=INK):
    """Ten table cells; the filled ones carry a bgcolor. Renders with images off."""
    n = 0 if score is None else max(0, min(10, int(round(score * 10))))
    cells = ""
    for i in range(10):
        c = filled_colour if i < n else PAPER2
        cells += (f'<td width="16" height="10" bgcolor="{c}" '
                  f'style="font-size:0;line-height:0;">&nbsp;</td>'
                  f'<td width="3" style="font-size:0;line-height:0;">&nbsp;</td>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr>{cells}</tr></table>')

def esc(s):
    return html.escape(str(s or "").strip())

def build(data, universe):
    picks = data["picks"]
    feat  = next((p for p in picks if p.get("is_featured")), picks[0])
    tk    = feat["ticker"]

    # percentile of the featured name within the universe, per bucket
    pct = {}
    for key, *_ in BUCKETS:
        col = f"score_{key}"
        vals = [float(r[col]) for r in universe if r.get(col) not in (None, "")]
        mine = feat["scores"].get(key)
        pct[key] = (sum(1 for v in vals if v < mine) / len(vals)) if (vals and mine is not None) else None

    # the funnel — how many names clear how many lenses, at the median bar
    keys = [k for k, *_ in BUCKETS]
    counts = {}
    for r in universe:
        try:
            n = sum(1 for k in keys if float(r[f"score_{k}"]) > 0.50)
        except (TypeError, ValueError, KeyError):
            continue
        counts[n] = counts.get(n, 0) + 1
    total = len(universe)
    clears = {k: sum(v for n, v in counts.items() if n >= k) for k in range(1, 6)}
    cleared_by_feat = sum(1 for k in keys if (feat["scores"].get(k) or 0) > 0.50)

    rows = ""
    for key, label, sub, wt in BUCKETS:
        # Bar AND label both read from the percentile, so they always agree.
        # The raw sub-score is a weighted blend of percentile ranks and is not
        # itself uniform, so using it for the bar and the percentile for the
        # label made a 6/10 bar sit next to "Top 40%".
        s = pct[key]
        rows += f"""
        <tr>
          <td style="padding:11px 0 0 0;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font:600 13px Helvetica,Arial,sans-serif;color:{INK};">{label}
                  <span style="font:400 11px Helvetica,Arial,sans-serif;color:{INK3};">&nbsp;{wt}</span>
                </td>
                <td align="right" style="font:400 12px Helvetica,Arial,sans-serif;color:{INK2};">{band(pct[key])}</td>
              </tr>
              <tr><td colspan="2" style="padding:4px 0 3px 0;">{bar(s)}</td></tr>
              <tr><td colspan="2" style="font:400 11px Helvetica,Arial,sans-serif;color:{INK3};padding-bottom:6px;">{sub}</td></tr>
            </table>
          </td>
        </tr>"""

    def kv(label, val):
        return f"""<td width="25%" style="padding:0 6px;">
          <div style="font:400 10px Helvetica,Arial,sans-serif;color:{INK3};letter-spacing:.08em;text-transform:uppercase;">{label}</div>
          <div style="font:600 16px Helvetica,Arial,sans-serif;color:{INK};padding-top:2px;">{val}</div></td>"""

    a = feat.get("analyst") or {}
    buys = (a.get("strong_buy") or 0) + (a.get("buy") or 0)
    arc_row = next((r for r in universe if r["ticker"] == tk), {})
    mom = fnum(arc_row.get("mom_12_1"), 0, "%")
    if mom != "—" and not mom.startswith("-"):
        mom = "+" + mom

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Opportunity — {esc(data.get('week_label'))}</title></head>
<body style="margin:0;padding:0;background:{PAPER2};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER2}">
<tr><td align="center" style="padding:24px 12px;">

<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:600px;max-width:600px;background:{PAPER};border:1px solid {RULE};">

  <tr><td style="padding:22px 28px 14px 28px;border-bottom:2px solid {INK};">
    <div style="font:400 10px Helvetica,Arial,sans-serif;color:{INK3};letter-spacing:.18em;text-transform:uppercase;">Stock Opportunity</div>
    <div style="font:400 12px Helvetica,Arial,sans-serif;color:{INK2};padding-top:5px;">{esc(data.get('week_label'))} &nbsp;·&nbsp; {esc(data.get('universe'))}</div>
  </td></tr>

  <tr><td style="padding:24px 28px 4px 28px;">
    <div style="font:700 40px Helvetica,Arial,sans-serif;color:{INK};letter-spacing:-.02em;">{esc(tk)}</div>
    <div style="font:400 14px Helvetica,Arial,sans-serif;color:{INK2};padding-top:3px;">{esc(feat.get('name'))} &nbsp;·&nbsp; {esc(feat.get('sector'))}</div>
    <div style="font:600 13px Helvetica,Arial,sans-serif;color:{ACCENT};padding-top:10px;">
      Clears {cleared_by_feat} of 5 measures &nbsp;·&nbsp; ranked {feat.get('rank')} of {total}
    </div>
  </td></tr>

  <tr><td style="padding:14px 28px 0 28px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
  </td></tr>

  <tr><td style="padding:20px 22px 4px 22px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      {kv("Price", "$" + fnum(feat.get("price")))}
      {kv("P/E", fnum(feat.get("pe_ttm"), 1, "x"))}
      {kv("12-Mth Return", mom)}
      {kv("Analysts Buy", f"{buys} of {a.get('total') or 0}")}
    </tr></table>
  </td></tr>

  <tr><td style="padding:20px 28px 0 28px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr><td style="border-left:3px solid {GREEN};padding:10px 0 10px 12px;background:#f1f5f2;">
        <div style="font:700 10px Helvetica,Arial,sans-serif;color:{GREEN};letter-spacing:.14em;text-transform:uppercase;">Bull case</div>
        <div style="font:400 13px/1.55 Helvetica,Arial,sans-serif;color:{INK2};padding-top:5px;">{esc(feat.get('bull_case'))}</div>
      </td></tr>
      <tr><td height="10" style="font-size:0;line-height:0;">&nbsp;</td></tr>
      <tr><td style="border-left:3px solid {RED};padding:10px 0 10px 12px;background:#f7f1f1;">
        <div style="font:700 10px Helvetica,Arial,sans-serif;color:{RED};letter-spacing:.14em;text-transform:uppercase;">Bear case</div>
        <div style="font:400 13px/1.55 Helvetica,Arial,sans-serif;color:{INK2};padding-top:5px;">{esc(feat.get('bear_case'))}</div>
      </td></tr>
    </table>
  </td></tr>

  <tr><td style="padding:22px 28px 0 28px;">
    <div style="font:400 10px Helvetica,Arial,sans-serif;color:{INK3};letter-spacing:.14em;text-transform:uppercase;padding-bottom:7px;">How we got here</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
           style="font:400 12px Helvetica,Arial,sans-serif;color:{INK2};">
      <tr><td style="padding:3px 0;">{total} companies screened</td><td align="right">{total}</td></tr>
      <tr><td style="padding:3px 0;">clear 3 of 5 measures</td><td align="right">{clears[3]}</td></tr>
      <tr><td style="padding:3px 0;">clear 4 of 5 measures</td><td align="right">{clears[4]}</td></tr>
      <tr><td style="padding:3px 0;font-weight:600;color:{INK};">clear all 5</td>
          <td align="right" style="font-weight:600;color:{INK};">{clears[5]}</td></tr>
    </table>
  </td></tr>

  <tr><td style="padding:20px 28px 24px 28px;">
    <div style="border-top:1px solid {RULE};padding-top:12px;font:400 10px/1.5 Helvetica,Arial,sans-serif;color:{INK3};">
      Measures are percentile ranks across the {esc(data.get('universe'))} universe, refreshed weekly.
      Value and Quality are ranked within sector; Momentum, Sentiment and Catalyst across the whole universe.
      For information only. Not investment advice, and not a recommendation to buy or sell any security.
      Past performance does not guarantee future results.
    </div>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

def main():
    with open(PAGE) as f:
        page = f.read()
    m = re.search(r'const DATA = (\{.*?\});\s*\n', page, re.DOTALL)
    if not m:
        raise SystemExit("Could not read DATA from " + PAGE)
    data = json.loads(m.group(1))
    universe = load_universe()
    os.makedirs("docs", exist_ok=True)
    with open(OUT, "w") as f:
        f.write(build(data, universe))
    feat = next((p for p in data["picks"] if p.get("is_featured")), data["picks"][0])
    print("=" * 60); print("Client emailer"); print("=" * 60)
    print(f"  {OUT}  featured {feat['ticker']} (rank {feat['rank']}) "
          f"from {len(universe)} scored names")

if __name__ == "__main__":
    main()
