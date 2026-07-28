# DLAC LATAM Weekly Monitor

Automated, bilingual (EN/ES) dashboard tracking markets, macroeconomics,
FDI, and institutional indicators for Latin America's **Core 6** economies:
Brazil, Mexico, Colombia, Argentina, Chile, and Peru.

**Live:** https://rebuitrago.github.io/dlac-latam-monitor/

A project of the **Dunning Latin America Centre (DLAC)**, EGADE Business
School, Tecnológico de Monterrey, in partnership with Henley Business
School, University of Reading.

---

## What it tracks

| Tab | Content | Source | Cadence |
|---|---|---|---|
| Overview | Weekly highlights (auto-generated), scorecard, FX, policy rates | Yahoo Finance, BIS, IMF | Weekly |
| Markets | Equity indexes, commodity futures | Yahoo Finance | Weekly |
| FX & Rates | Exchange rates, central bank policy rates | Yahoo Finance, **BIS Data Portal** | Weekly |
| Macro | GDP, inflation, unemployment | IMF WEO API | Weekly (annual data) |
| FDI & Institutions | FDI net inflows (10-yr trend, % GDP), Worldwide Governance Indicators | World Bank API | Weekly (annual data) |
| Market Signals | FX realized volatility, 4-week trends, policy direction — **computed, no analyst judgment** | Derived | Weekly |

## Design principles (v2)

1. **No hand-maintained numbers.** Policy rates come from the BIS API. If a
   fetch fails, the last good value is carried forward and visibly flagged
   `STALE` in the UI.
2. **Fallback chains, honest failures.** Each equity series has backup
   tickers (clearly labeled USD ETF proxies where needed). A series with no
   working source displays "Feed unavailable" instead of silent blanks.
3. **All editorial text is rule-based.** The weekly highlights are generated
   from the data itself, in both languages. Nothing on the page can silently
   go stale.
4. **History accumulates.** Each run appends a snapshot to
   `docs/data/history.json` (capped at 260 weeks), enabling trend sparklines.

## Architecture

```
fetch_data.py            # data pipeline (Python)
data/latam_data.json     # generated output
docs/index.html          # dashboard (GitHub Pages, no build step)
docs/data/latam_data.json
docs/data/history.json   # weekly snapshots
.github/workflows/weekly_update.yml   # Mondays 07:00 UTC + manual trigger
```

The GitHub Action runs every Monday, refreshes all series, writes a data
quality report to the job summary, and commits the JSON. To force a refresh:
**Actions → Weekly LATAM Data Refresh → Run workflow**.

## Local run

```bash
pip install yfinance requests pandas
python fetch_data.py
# open docs/index.html via any local server
```

## Sources

Yahoo Finance (yfinance) · BIS Data Portal (WS_CBPOL) · IMF World Economic
Outlook API · World Bank Open Data API (FDI BoP, WGI). All public and free.

---

For informational and academic purposes only. Not investment advice.
