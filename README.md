# DLAC LATAM Weekly Monitor

**Dunning Latin America Centre · EGADE Business School, Tecnológico de Monterrey**

An automated, bilingual (EN/ES) interactive dashboard tracking equity markets, FX rates,
commodity prices, and macroeconomic indicators for Latin America's Core 6 economies:
Brazil, Mexico, Colombia, Argentina, Chile, and Peru.

---

## Architecture

```
dlac-latam-monitor/
├── fetch_data.py          ← Python script: pulls data from public APIs
├── requirements.txt       ← Python dependencies
├── data/
│   └── latam_data.json    ← Generated data file (auto-updated weekly)
├── docs/
│   ├── index.html         ← Interactive dashboard (GitHub Pages)
│   └── data/
│       └── latam_data.json← Copy served by GitHub Pages
└── .github/
    └── workflows/
        └── weekly_update.yml ← GitHub Actions: runs every Monday 07:00 UTC
```

### Data Flow

```
GitHub Actions (Monday 07:00 UTC)
        │
        ▼
fetch_data.py
        │
        ├── Yahoo Finance (yfinance)  → Equity indexes, FX rates, Commodities
        ├── IMF Data API              → GDP growth, Inflation forecasts
        └── World Bank API            → Supplementary macro data
        │
        ▼
data/latam_data.json  ──copy──▶  docs/data/latam_data.json
                                          │
                                          ▼
                                 docs/index.html
                                 (GitHub Pages URL)
```

---

## Data Sources

| Data Type | Source | API | Cost |
|---|---|---|---|
| Equity indexes | Yahoo Finance | `yfinance` Python lib | Free |
| FX rates | Yahoo Finance | `yfinance` Python lib | Free |
| Commodity prices | Yahoo Finance | `yfinance` Python lib | Free |
| GDP growth forecasts | IMF World Economic Outlook | REST API | Free |
| Inflation forecasts | IMF World Economic Outlook | REST API | Free |
| Unemployment | IMF / World Bank | REST API | Free |
| Policy interest rates | Static (manual update) | N/A | Free |

> **Policy rates note:** No free real-time API exists for official central bank policy rates.
> These are maintained as a static baseline in `fetch_data.py → _load_policy_rates()`.
> Update them after central bank announcements (takes ~2 minutes).

---

## Setup Instructions

### 1. Fork or clone this repository

```bash
git clone https://github.com/YOUR_USERNAME/dlac-latam-monitor.git
cd dlac-latam-monitor
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the data fetcher manually (first time)

```bash
python fetch_data.py
```

This generates `data/latam_data.json` and `docs/data/latam_data.json`.

### 4. Open the dashboard locally

Open `docs/index.html` in any browser. The dashboard reads from `docs/data/latam_data.json`.

---

## GitHub Pages Deployment (Online Sharing)

### Step 1 — Push to GitHub

```bash
git add .
git commit -m "Initial setup"
git push origin main
```

### Step 2 — Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** → **Pages** (left sidebar)
3. Under **Source**, select:
   - Branch: `main`
   - Folder: `/docs`
4. Click **Save**

Your dashboard will be live at:
```
https://YOUR_USERNAME.github.io/dlac-latam-monitor/
```
(Takes 1–2 minutes to deploy on first setup.)

### Step 3 — Verify GitHub Actions is enabled

1. Go to the **Actions** tab in your repository
2. You should see the **Weekly LATAM Data Refresh** workflow
3. Click **Run workflow** to test it manually

The workflow runs automatically every **Monday at 07:00 UTC** (02:00 Mexico City time),
so the dashboard is updated before business hours in LATAM.

---

## Manual Data Update

To update data on demand (outside the Monday schedule):

**Option A — GitHub Actions (recommended)**
1. Go to **Actions** tab → **Weekly LATAM Data Refresh**
2. Click **Run workflow** → **Run workflow**

**Option B — Local run**
```bash
python fetch_data.py
git add data/ docs/data/
git commit -m "Manual data update $(date)"
git push
```

---

## Updating Policy Rates

When a central bank changes its rate, update the `_load_policy_rates()` function
in `fetch_data.py`:

```python
def _load_policy_rates():
    return {
        "Brazil":    {"rate": 14.75, "as_of": "2026-04-29", "direction": "cutting", ...},
        "Mexico":    {"rate": 9.00,  "as_of": "2026-05-08", "direction": "cutting", ...},
        # ... update rate and as_of date
    }
```

Valid `direction` values: `"cutting"`, `"hold"`, `"easing"`, `"hiking"`

---

## Dashboard Features

- **6 tabs**: Overview, Markets, FX & Rates, Macro, Risk & Trends, Outlook
- **Bilingual**: Full EN/ES toggle with one click
- **Print-ready**: All sections expand cleanly for PDF printing
- **Responsive**: Works on desktop, tablet, and mobile
- **Auto-refresh**: "Refresh Data" button reloads the JSON without page reload
- **Data freshness indicator**: Shows how many hours ago data was updated
- **Animated bar charts**: YTD performance, inflation, GDP comparisons

---

## Extending to Additional Countries

To add more LATAM countries, update these dicts in `fetch_data.py`:
- `EQUITY_TICKERS` — add Yahoo Finance ticker
- `FX_TICKERS` — add Yahoo Finance FX ticker
- `IMF_COUNTRIES` — add ISO3 code
- `RISK_DATA` / `RISK_DATA_ES` in `docs/index.html` — add risk assessment
- `OUTLOOKS` in `docs/index.html` — add country narrative

---

## Contact

**DLAC — Dunning Latin America Centre**
EGADE Business School · Tecnológico de Monterrey
In partnership with Henley Business School · University of Reading

---

*This dashboard is for informational and academic purposes only.
Data sourced from Yahoo Finance, IMF, and World Bank public APIs.
Not investment advice.*
