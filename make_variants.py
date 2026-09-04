#!/usr/bin/env python3
"""
Presentation variants — runs AFTER screener_v2.py, changes no scoring.

screener_v2.py writes docs/index.html. This reads it back, then publishes three
views of the same ranking so they can be compared side by side:

  docs/index.html       Baseline      featured pick = rank 1, as scored
  docs/featured.html    Upside >= 15% featured pick = best-ranked name that also
                                      clears the upside floor
  docs/conviction.html  Conviction    same featured rule, and the two forecast-based
                                      hero metrics are replaced with realised ones:
                                      12-month return, and analyst buy count

The ranking is IDENTICAL in all three. Only which pick fills the hero panel, and
what the hero panel displays, changes. No sub-score, weight or rank is touched.
"""
import json, re, copy, glob, csv, os
from datetime import datetime

PAGE         = "docs/index.html"
UPSIDE_FLOOR = 15.0

VARIANTS = [
    ("index.html",      "Baseline",      "baseline"),
    ("featured.html",   "Upside ≥ 15%", "floor"),
    ("conviction.html", "Conviction",    "conviction"),
]

HERO_OVERRIDE = """
<script>
// Conviction view: replace the two forecast-based hero metrics with realised
// figures. Wraps renderHero so clicking a watchlist row keeps the override.
(function () {
  var orig = renderHero;
  window.renderHero = function (p) {
    orig(p);
    var a = document.getElementById('h-target');
    var b = document.getElementById('h-upside');
    if (a) a.textContent = p.hero_alt_1 || '\\u2014';
    if (b) b.textContent = p.hero_alt_2 || '\\u2014';
  };
  window.renderHero(picks.find(function (p) { return p.is_featured; }) || picks[0]);
})();
</script>
"""

def nav_html(active):
    out = []
    for fn, label, key in VARIANTS:
        style = ("background:var(--ink);color:var(--paper);border-color:var(--ink);"
                 if key == active else "")
        out.append(f'<a href="./{fn}" class="masthead-label" '
                   f'style="text-decoration:none;{style}">{label}</a>')
    return "".join(out)

def choose_featured(picks, floor):
    """Best-ranked name that also clears the upside floor. picks are rank-ordered."""
    for p in picks:
        u = p.get("upside_pct")
        if u is not None and u >= floor:
            return p["rank"]
    return picks[0]["rank"]

def latest_archive():
    files = sorted(glob.glob("data/v2_scores_*.csv"))
    return files[-1] if files else None

def add_hero_alts(picks):
    """12-month return and analyst buy count, for the conviction view."""
    arc = {}
    path = latest_archive()
    if path:
        with open(path) as f:
            for r in csv.DictReader(f):
                arc[r["ticker"]] = r
    for p in picks:
        row = arc.get(p["ticker"], {})
        try:
            p["hero_alt_1"] = f'{float(row.get("mom_12_1")):+.1f}%'
        except (TypeError, ValueError):
            p["hero_alt_1"] = "—"
        a = p.get("analyst") or {}
        total = a.get("total") or 0
        buys  = (a.get("strong_buy") or 0) + (a.get("buy") or 0)
        p["hero_alt_2"] = f"{buys} of {total}" if total else "—"

def build(template, data, key):
    d = copy.deepcopy(data)
    picks = d["picks"]

    if key in ("floor", "conviction"):
        keep = choose_featured(picks, UPSIDE_FLOOR)
        for p in picks:
            p["is_featured"] = (p["rank"] == keep)

    if key == "conviction":
        d["week_label"] = d["week_label"] + " · conviction view"

    # strip any nav injected by a previous run so this stays idempotent
    html = re.sub(r'<a href="\./(?:index|featured|conviction)\.html" '
                  r'class="masthead-label"[^>]*>.*?</a>', "", template, flags=re.DOTALL)
    m = re.search(r'const DATA = \{.*?\};', html, re.DOTALL)
    if not m:
        raise ValueError("DATA blob not found")
    html = html[:m.start()] + f"const DATA = {json.dumps(d)};" + html[m.end():]

    anchor = '<div class="masthead-right">'
    html = html.replace(anchor, anchor + nav_html(key), 1)

    if key == "conviction":
        html = html.replace('<span class="key-metric-label">Avg. Analyst Target</span>',
                            '<span class="key-metric-label">12-Month Return</span>', 1)
        html = html.replace('<span class="key-metric-label">Upside</span>',
                            '<span class="key-metric-label">Analyst Conviction</span>', 1)
        html = html.replace("</body>", HERO_OVERRIDE + "</body>", 1)
    return html

def main():
    with open(PAGE) as f:
        template = f.read()
    m = re.search(r'const DATA = (\{.*?\});\s*\n', template, re.DOTALL)
    if not m:
        raise SystemExit("Could not read DATA from " + PAGE)
    data = json.loads(m.group(1))
    add_hero_alts(data["picks"])

    print("=" * 60)
    print("Publishing presentation variants")
    print("=" * 60)
    for fn, label, key in VARIANTS:
        html = build(template, data, key)
        out = os.path.join("docs", fn)
        with open(out, "w") as f:
            f.write(html)
        d2 = json.loads(re.search(r'const DATA = (\{.*?\});\s*\n', html, re.DOTALL).group(1))
        feat = [p for p in d2["picks"] if p["is_featured"]][0]
        print(f"  {out:<24} {label:<14} featured: {feat['ticker']:<6} "
              f"rank {feat['rank']:<3} upside {feat.get('upside_pct')}%")
    print("\nRanking is identical across all three - only the hero panel differs.")

if __name__ == "__main__":
    main()
