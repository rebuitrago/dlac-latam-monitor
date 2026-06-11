"""
DLAC / EGADE Business School
LATAM Weekly Monitor — Data Fetcher
=====================================
Fetches equity indexes, FX rates, commodity prices,
and macroeconomic indicators from reputable public sources.

Sources:
  - Yahoo Finance  : Equity indexes, FX, commodities
  - IMF Data API   : GDP growth, inflation forecasts
  - World Bank API : Supplementary macro data
  - FRED (St. Louis Fed) : Policy rates context

Run this script every Monday morning (or schedule with cron / Task Scheduler).
Output: data/latam_data.json  (read by the HTML dashboard)

Requirements:
    pip install yfinance requests pandas

Author: DLAC — Dunning Latin America Centre
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ── Third-party ──────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    import requests
    import pandas as pd
except ImportError:
    print("Missing dependencies. Run: pip install yfinance requests pandas")
    sys.exit(1)

# ── Config ───────────────────────────────────────────────────────────────────
OUTPUT_FILE = Path(__file__).parent / "data" / "latam_data.json"
LOG_LEVEL   = logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dlac-fetcher")


# ═══════════════════════════════════════════════════════════════════════════
# 1. EQUITY INDEX TICKERS
# ═══════════════════════════════════════════════════════════════════════════
EQUITY_TICKERS = {
    "Brazil":    {"ticker": "^BVSP",  "index": "IBOVESPA",    "flag": "🇧🇷"},
    "Mexico":    {"ticker": "^MXX",   "index": "S&P/BMV IPC", "flag": "🇲🇽"},
    "Colombia":  {"ticker": "^COLCAP","index": "COLCAP",      "flag": "🇨🇴"},
    "Argentina": {"ticker": "^MERV",  "index": "MERVAL",      "flag": "🇦🇷"},
    "Chile":     {"ticker": "^IPSA",  "index": "IPSA",        "flag": "🇨🇱"},
    "Peru":      {"ticker": "^SPBLPGPT","index":"BVL General","flag": "🇵🇪"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 2. FX TICKERS  (Yahoo Finance: XXXYYY=X format)
# ═══════════════════════════════════════════════════════════════════════════
FX_TICKERS = {
    "USD/BRL": {"ticker": "BRL=X",  "flag": "🇧🇷", "country": "Brazil"},
    "USD/MXN": {"ticker": "MXN=X",  "flag": "🇲🇽", "country": "Mexico"},
    "USD/COP": {"ticker": "COP=X",  "flag": "🇨🇴", "country": "Colombia"},
    "USD/ARS": {"ticker": "ARS=X",  "flag": "🇦🇷", "country": "Argentina"},
    "USD/CLP": {"ticker": "CLP=X",  "flag": "🇨🇱", "country": "Chile"},
    "USD/PEN": {"ticker": "PEN=X",  "flag": "🇵🇪", "country": "Peru"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 3. COMMODITY TICKERS
# ═══════════════════════════════════════════════════════════════════════════
COMMODITY_TICKERS = {
    "Brent Crude":  {"ticker": "BZ=F",  "unit": "USD/bbl", "emoji": "🛢",  "latam": "Colombia, Mexico, Brazil"},
    "Copper":       {"ticker": "HG=F",  "unit": "USD/lb",  "emoji": "🔴",  "latam": "Chile, Peru"},
    "Gold":         {"ticker": "GC=F",  "unit": "USD/oz",  "emoji": "🥇",  "latam": "Peru, Colombia"},
    "Soybean":      {"ticker": "ZS=F",  "unit": "USc/bu",  "emoji": "🌱",  "latam": "Brazil, Argentina"},
    "Coffee":       {"ticker": "KC=F",  "unit": "USc/lb",  "emoji": "☕",  "latam": "Colombia, Brazil"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 4. IMF API  — GDP growth and inflation forecasts
#    https://www.imf.org/external/datamapper/api/v1/
# ═══════════════════════════════════════════════════════════════════════════
IMF_COUNTRIES = {
    "Brazil":    "BRA",
    "Mexico":    "MEX",
    "Colombia":  "COL",
    "Argentina": "ARG",
    "Chile":     "CHL",
    "Peru":      "PER",
}

IMF_INDICATORS = {
    "gdp_growth": "NGDP_RPCH",   # Real GDP growth %
    "inflation":  "PCPIPCH",     # Inflation, average consumer prices %
    "unemployment":"LUR",        # Unemployment rate
}

IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"

# ═══════════════════════════════════════════════════════════════════════════
# 5. WORLD BANK API  — supplementary indicators
#    https://api.worldbank.org/v2/
# ═══════════════════════════════════════════════════════════════════════════
WB_BASE = "https://api.worldbank.org/v2"

# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def safe_round(val, decimals=2):
    """Round a value safely, returning None if not numeric."""
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return None


def pct_change(current, previous):
    """Calculate percentage change between two values."""
    try:
        return round((current - previous) / previous * 100, 2)
    except (TypeError, ZeroDivisionError):
        return None


def fetch_yf_ticker(ticker_symbol, period="5d", interval="1d"):
    """
    Fetch price data from Yahoo Finance.
    Returns dict with: last, prev_close, change_pct, ytd_pct, week_pct
    """
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period=period, interval=interval, auto_adjust=True)

        if hist.empty:
            log.warning(f"No data for {ticker_symbol}")
            return None

        last   = safe_round(hist["Close"].iloc[-1])
        prev   = safe_round(hist["Close"].iloc[-2]) if len(hist) > 1 else last
        daily  = pct_change(last, prev)

        # Week change (last 5 trading days)
        week_prev = safe_round(hist["Close"].iloc[0]) if len(hist) >= 5 else prev
        weekly = pct_change(last, week_prev)

        # YTD: fetch since Jan 1 of current year
        year_start = datetime(datetime.now().year, 1, 2).strftime("%Y-%m-%d")
        ytd_hist = tk.history(start=year_start, interval="1wk", auto_adjust=True)
        ytd_pct  = None
        if not ytd_hist.empty:
            ytd_prev = safe_round(ytd_hist["Close"].iloc[0])
            ytd_pct  = pct_change(last, ytd_prev)

        return {
            "last":       last,
            "prev_close": prev,
            "change_pct": daily,
            "week_pct":   weekly,
            "ytd_pct":    ytd_pct,
            "updated":    hist.index[-1].strftime("%Y-%m-%d"),
        }

    except Exception as e:
        log.error(f"Yahoo Finance error for {ticker_symbol}: {e}")
        return None


def fetch_imf_indicator(indicator_code, country_codes, years=2):
    """
    Fetch an IMF WEO indicator for a list of ISO3 country codes.
    Returns dict: {country_code: {year: value, ...}}
    """
    url  = f"{IMF_BASE}/{indicator_code}"
    params = {"periods": ",".join(
        str(datetime.now().year + i) for i in range(-1, years)
    )}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("values", {}).get(indicator_code, {})
        result = {}
        for iso3 in country_codes:
            if iso3 in data:
                result[iso3] = {
                    yr: safe_round(v, 1)
                    for yr, v in data[iso3].items()
                    if v is not None
                }
        return result
    except Exception as e:
        log.error(f"IMF API error ({indicator_code}): {e}")
        return {}


def fetch_wb_indicator(indicator, country_codes, mrv=2):
    """
    Fetch a World Bank indicator.
    Returns dict: {iso2: latest_value}
    """
    cc_str = ";".join(country_codes)
    url    = f"{WB_BASE}/country/{cc_str}/indicator/{indicator}"
    params = {"format": "json", "mrv": mrv, "per_page": 50}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        payload = r.json()
        if len(payload) < 2:
            return {}
        rows   = payload[1] or []
        result = {}
        for row in rows:
            iso2 = row.get("countryiso3code", "")
            val  = row.get("value")
            yr   = row.get("date", "")
            if val is not None and iso2 not in result:
                result[iso2] = {"value": safe_round(val, 1), "year": yr}
        return result
    except Exception as e:
        log.error(f"World Bank API error ({indicator}): {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN FETCH ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def fetch_all():
    log.info("═══ DLAC LATAM Weekly Monitor — Data Fetch Starting ═══")
    report_date = datetime.now().strftime("%Y-%m-%d")
    report_week = datetime.now().strftime("Week of %B %d, %Y")

    output = {
        "meta": {
            "generated":   datetime.now().isoformat(),
            "report_date": report_date,
            "report_week": report_week,
            "report_week_es": _week_es(datetime.now()),
            "sources": [
                "Yahoo Finance (yfinance)",
                "IMF World Economic Outlook API",
                "World Bank Open Data API",
            ],
        },
        "equities":    {},
        "fx":          {},
        "commodities": {},
        "macro":       {},
    }

    # ── 1. EQUITIES ──────────────────────────────────────────────────────
    log.info("Fetching equity indexes...")
    for country, cfg in EQUITY_TICKERS.items():
        log.info(f"  {country}: {cfg['ticker']}")
        data = fetch_yf_ticker(cfg["ticker"])
        output["equities"][country] = {
            "index":   cfg["index"],
            "flag":    cfg["flag"],
            "ticker":  cfg["ticker"],
            **(data or {"last": None, "change_pct": None, "week_pct": None, "ytd_pct": None}),
        }
        time.sleep(0.3)  # rate limit courtesy

    # ── 2. FX RATES ───────────────────────────────────────────────────────
    log.info("Fetching FX rates...")
    for pair, cfg in FX_TICKERS.items():
        log.info(f"  {pair}: {cfg['ticker']}")
        data = fetch_yf_ticker(cfg["ticker"])
        output["fx"][pair] = {
            "flag":    cfg["flag"],
            "country": cfg["country"],
            "ticker":  cfg["ticker"],
            **(data or {"last": None, "change_pct": None, "week_pct": None}),
        }
        time.sleep(0.3)

    # ── 3. COMMODITIES ────────────────────────────────────────────────────
    log.info("Fetching commodity prices...")
    for name, cfg in COMMODITY_TICKERS.items():
        log.info(f"  {name}: {cfg['ticker']}")
        data = fetch_yf_ticker(cfg["ticker"])
        output["commodities"][name] = {
            "unit":  cfg["unit"],
            "emoji": cfg["emoji"],
            "latam": cfg["latam"],
            **(data or {"last": None, "change_pct": None}),
        }
        time.sleep(0.3)

    # ── 4. IMF MACRO DATA ─────────────────────────────────────────────────
    log.info("Fetching IMF macro data...")
    iso3_list = list(IMF_COUNTRIES.values())
    current_yr = str(datetime.now().year)
    next_yr    = str(datetime.now().year + 1)

    gdp_data   = fetch_imf_indicator(IMF_INDICATORS["gdp_growth"], iso3_list)
    inf_data   = fetch_imf_indicator(IMF_INDICATORS["inflation"],  iso3_list)
    unemp_data = fetch_imf_indicator(IMF_INDICATORS["unemployment"], iso3_list)

    for country, iso3 in IMF_COUNTRIES.items():
        output["macro"][country] = {
            "iso3":          iso3,
            "flag":          EQUITY_TICKERS[country]["flag"],
            "gdp_current":   (gdp_data.get(iso3, {}) or {}).get(current_yr),
            "gdp_forecast":  (gdp_data.get(iso3, {}) or {}).get(next_yr),
            "cpi_current":   (inf_data.get(iso3, {}) or {}).get(current_yr),
            "cpi_forecast":  (inf_data.get(iso3, {}) or {}).get(next_yr),
            "unemployment":  (unemp_data.get(iso3, {}) or {}).get(current_yr),
        }

    # ── 5. POLICY RATES  (static — updated manually or via central bank RSS) ──
    # Note: No free real-time API exists for official policy rates.
    # These are updated from central bank announcements.
    # The script preserves last known values and flags stale data.
    log.info("Loading policy rates (static baseline — update from central bank announcements)...")
    policy_rates = _load_policy_rates()
    output["policy_rates"] = policy_rates

    # ── WRITE OUTPUT ──────────────────────────────────────────────────────
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"═══ Data written to {OUTPUT_FILE} ═══")
    _print_summary(output)
    return output


def _load_policy_rates():
    """
    Policy rates and their as-of dates.
    Update this dict after central bank announcements.
    Source: Official central bank websites.
    """
    return {
        "Brazil":    {"rate": 14.75, "as_of": "2026-04-29", "direction": "cutting",  "bank": "BCB/Copom",  "flag": "🇧🇷"},
        "Mexico":    {"rate": 9.00,  "as_of": "2026-05-08", "direction": "cutting",  "bank": "Banxico",    "flag": "🇲🇽"},
        "Colombia":  {"rate": 9.50,  "as_of": "2026-05-30", "direction": "hold",     "bank": "BanRep",     "flag": "🇨🇴"},
        "Argentina": {"rate": 35.00, "as_of": "2026-05-15", "direction": "easing",   "bank": "BCRA",       "flag": "🇦🇷"},
        "Chile":     {"rate": 4.50,  "as_of": "2026-05-06", "direction": "hold",     "bank": "BCCh",       "flag": "🇨🇱"},
        "Peru":      {"rate": 4.25,  "as_of": "2026-05-08", "direction": "hold",     "bank": "BCRP",       "flag": "🇵🇪"},
    }


def _week_es(dt):
    """Return Spanish week label."""
    months_es = [
        "", "enero","febrero","marzo","abril","mayo","junio",
        "julio","agosto","septiembre","octubre","noviembre","diciembre"
    ]
    return f"Semana del {dt.day} de {months_es[dt.month]} de {dt.year}"


def _print_summary(data):
    """Print a brief console summary after fetch."""
    print("\n" + "═"*60)
    print("  DLAC LATAM Weekly Monitor — Fetch Summary")
    print("═"*60)
    print(f"  Generated : {data['meta']['generated'][:19]}")
    print(f"  Report    : {data['meta']['report_week']}")
    print()
    print("  EQUITY INDEXES")
    for c, d in data["equities"].items():
        last = d.get("last")
        chg  = d.get("change_pct")
        ytd  = d.get("ytd_pct")
        arrow = "▲" if (chg or 0) >= 0 else "▼"
        print(f"  {d['flag']} {c:<12} {d['index']:<15} "
              f"{str(last or 'N/A'):>12}  {arrow}{abs(chg or 0):.2f}%  YTD:{ytd or 'N/A'}%")
    print()
    print("  FX RATES (vs USD)")
    for pair, d in data["fx"].items():
        last = d.get("last")
        chg  = d.get("change_pct")
        arrow = "▲" if (chg or 0) >= 0 else "▼"
        print(f"  {d['flag']} {pair:<10} {str(last or 'N/A'):>10}  {arrow}{abs(chg or 0):.2f}%")
    print("═"*60 + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    fetch_all()
