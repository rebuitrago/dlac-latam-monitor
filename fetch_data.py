"""
DLAC / EGADE Business School
LATAM Weekly Monitor — Data Fetcher (v2)
========================================
Fetches equity indexes, FX rates, commodity prices, policy rates,
macroeconomic indicators, FDI flows, and governance indicators
from reputable public sources. Fully automated — no manual fields.

Sources:
  - Yahoo Finance (yfinance) : Equity indexes, FX, commodities
  - BIS Data Portal API      : Central bank policy rates (daily series)
  - IMF Data Mapper API      : GDP growth, inflation, unemployment (WEO)
  - World Bank Open Data API : FDI inflows, Worldwide Governance Indicators

Design principles (v2):
  1. No hand-maintained numbers. Policy rates come from the BIS API;
     if the API fails, the previous published value is carried forward
     and explicitly flagged as stale in the JSON (surfaced in the UI).
  2. Fallback ticker chains for equity feeds. If a local index is not
     available on Yahoo Finance, a clearly labeled USD ETF proxy is
     used; if nothing works, the series is marked unavailable rather
     than silently null.
  3. Weekly history accumulates in docs/data/history.json so the
     dashboard can draw trend sparklines. Capped at 260 weeks.
  4. All editorial text ("highlights") is rule-based, generated from
     the data itself, bilingual EN/ES.

Output:
  data/latam_data.json          (also copied to docs/data/ by CI)
  docs/data/history.json        (appended weekly)

Requirements:
    pip install yfinance requests pandas

Author: DLAC — Dunning Latin America Centre
"""

import io
import json
import math
import sys
import time
import logging
from datetime import datetime
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
ROOT         = Path(__file__).parent
OUTPUT_FILE  = ROOT / "data" / "latam_data.json"
DOCS_OUTPUT  = ROOT / "docs" / "data" / "latam_data.json"
HISTORY_FILE = ROOT / "docs" / "data" / "history.json"
HISTORY_CAP  = 260   # weeks (~5 years)
LOG_LEVEL    = logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dlac-fetcher")

COUNTRIES = ["Brazil", "Mexico", "Colombia", "Argentina", "Chile", "Peru"]

FLAGS = {
    "Brazil": "🇧🇷", "Mexico": "🇲🇽", "Colombia": "🇨🇴",
    "Argentina": "🇦🇷", "Chile": "🇨🇱", "Peru": "🇵🇪",
}

ISO3 = {
    "Brazil": "BRA", "Mexico": "MEX", "Colombia": "COL",
    "Argentina": "ARG", "Chile": "CHL", "Peru": "PER",
}

ISO2 = {
    "Brazil": "BR", "Mexico": "MX", "Colombia": "CO",
    "Argentina": "AR", "Chile": "CL", "Peru": "PE",
}

# ═══════════════════════════════════════════════════════════════════════════
# 1. EQUITY SOURCES — ordered fallback chains per country.
#    proxy=True means a USD-denominated ETF proxy, labeled as such in the UI.
# ═══════════════════════════════════════════════════════════════════════════
EQUITY_SOURCES = {
    "Brazil": [
        {"ticker": "^BVSP", "index": "IBOVESPA", "proxy": False},
        {"ticker": "EWZ",   "index": "iShares MSCI Brazil ETF (USD)", "proxy": True},
    ],
    "Mexico": [
        {"ticker": "^MXX", "index": "S&P/BMV IPC", "proxy": False},
        {"ticker": "EWW",  "index": "iShares MSCI Mexico ETF (USD)", "proxy": True},
    ],
    "Colombia": [
        {"ticker": "^COLCAP",    "index": "MSCI COLCAP", "proxy": False},
        {"ticker": "ICOLCAP.CL", "index": "iShares COLCAP (BVC)", "proxy": True},
    ],
    "Argentina": [
        {"ticker": "^MERV", "index": "S&P MERVAL", "proxy": False},
        {"ticker": "ARGT",  "index": "Global X MSCI Argentina ETF (USD)", "proxy": True},
    ],
    "Chile": [
        {"ticker": "^IPSA", "index": "S&P IPSA", "proxy": False},
        {"ticker": "ECH",   "index": "iShares MSCI Chile ETF (USD)", "proxy": True},
    ],
    "Peru": [
        {"ticker": "^SPBLPGPT", "index": "S&P/BVL Peru General", "proxy": False},
        {"ticker": "EPU",       "index": "iShares MSCI Peru ETF (USD)", "proxy": True},
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# 2. FX TICKERS  (Yahoo Finance: XXX=X → USD/XXX)
# ═══════════════════════════════════════════════════════════════════════════
FX_TICKERS = {
    "USD/BRL": {"ticker": "BRL=X", "country": "Brazil"},
    "USD/MXN": {"ticker": "MXN=X", "country": "Mexico"},
    "USD/COP": {"ticker": "COP=X", "country": "Colombia"},
    "USD/ARS": {"ticker": "ARS=X", "country": "Argentina"},
    "USD/CLP": {"ticker": "CLP=X", "country": "Chile"},
    "USD/PEN": {"ticker": "PEN=X", "country": "Peru"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 3. COMMODITY TICKERS
# ═══════════════════════════════════════════════════════════════════════════
COMMODITY_TICKERS = {
    "Brent Crude": {"ticker": "BZ=F", "unit": "USD/bbl", "emoji": "🛢",  "latam": "Colombia, Mexico, Brazil"},
    "Copper":      {"ticker": "HG=F", "unit": "USD/lb",  "emoji": "🔴", "latam": "Chile, Peru"},
    "Gold":        {"ticker": "GC=F", "unit": "USD/oz",  "emoji": "🥇", "latam": "Peru, Colombia"},
    "Soybean":     {"ticker": "ZS=F", "unit": "USc/bu",  "emoji": "🌱", "latam": "Brazil, Argentina"},
    "Coffee":      {"ticker": "KC=F", "unit": "USc/lb",  "emoji": "☕", "latam": "Colombia, Brazil"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 4. IMF DATA MAPPER API — WEO indicators
# ═══════════════════════════════════════════════════════════════════════════
IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"
IMF_INDICATORS = {
    "gdp_growth":  "NGDP_RPCH",
    "inflation":   "PCPIPCH",
    "unemployment": "LUR",
}

# ═══════════════════════════════════════════════════════════════════════════
# 5. WORLD BANK API — FDI and governance
# ═══════════════════════════════════════════════════════════════════════════
WB_BASE = "https://api.worldbank.org/v2"
WB_FDI_USD  = "BX.KLT.DINV.CD.WD"      # FDI net inflows, current US$ (BoP)
WB_FDI_GDP  = "BX.KLT.DINV.WD.GD.ZS"   # FDI net inflows, % of GDP
# WGI codes were renamed by the World Bank (2024 revamp): old "GE.EST" style
# codes are archived; current codes live in source=3 as "GOV_WGI_*.EST".
WB_WGI = {
    "gov_effectiveness":  "GOV_WGI_GE.EST",
    "regulatory_quality": "GOV_WGI_RQ.EST",
    "rule_of_law":        "GOV_WGI_RL.EST",
    "control_corruption": "GOV_WGI_CC.EST",
    "political_stability": "GOV_WGI_PV.EST",
    "voice_accountability": "GOV_WGI_VA.EST",
}

# ═══════════════════════════════════════════════════════════════════════════
# 6. BIS DATA PORTAL — central bank policy rates (daily series WS_CBPOL)
#    Two endpoint generations tried in order; CSV parsed generically.
# ═══════════════════════════════════════════════════════════════════════════
BIS_ENDPOINTS = [
    "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.{cc}?lastNObservations=130&format=csv",
    "https://stats.bis.org/api/v1/data/WS_CBPOL_D/D.{cc}/all?lastNObservations=130&format=csv",
]

CENTRAL_BANKS = {
    "Brazil":    {"bank": "BCB/Copom", "target": "3.0% ±1.5"},
    "Mexico":    {"bank": "Banxico",   "target": "3.0% ±1"},
    "Colombia":  {"bank": "BanRep",    "target": "3.0% ±1"},
    "Argentina": {"bank": "BCRA",      "target": "—"},
    "Chile":     {"bank": "BCCh",      "target": "3.0% ±1"},
    "Peru":      {"bank": "BCRP",      "target": "2.0% ±1"},
}

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def safe_round(val, decimals=2):
    try:
        v = float(val)
        if math.isnan(v):
            return None
        return round(v, decimals)
    except (TypeError, ValueError):
        return None


def pct_change(current, previous):
    try:
        return round((current - previous) / previous * 100, 2)
    except (TypeError, ZeroDivisionError):
        return None


def _retry(fn, attempts=3, base_delay=2.0, label=""):
    """Run fn() with retries and exponential backoff. Returns None on failure."""
    for i in range(attempts):
        try:
            result = fn()
            if result is not None:
                return result
        except Exception as e:
            log.warning(f"  attempt {i+1}/{attempts} failed{f' ({label})' if label else ''}: {e}")
        if i < attempts - 1:
            time.sleep(base_delay * (2 ** i))
    return None


def fetch_yf_series(ticker_symbol, period="4mo"):
    """
    Fetch daily close series from Yahoo Finance with retries.
    Returns a pandas Series (index=dates) or None.
    """
    def _pull():
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period=period, interval="1d", auto_adjust=True)
        if hist.empty or "Close" not in hist:
            return None
        s = hist["Close"].dropna()
        return s if len(s) >= 2 else None
    return _retry(_pull, label=ticker_symbol)


def fetch_yf_ytd(ticker_symbol, last_value):
    """YTD % change using weekly bars from Jan 1."""
    def _pull():
        year_start = datetime(datetime.now().year, 1, 2).strftime("%Y-%m-%d")
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(start=year_start, interval="1wk", auto_adjust=True)
        if hist.empty:
            return None
        base = safe_round(hist["Close"].dropna().iloc[0])
        return pct_change(last_value, base)
    return _retry(_pull, attempts=2, label=f"{ticker_symbol} YTD")


def series_to_quote(s):
    """Convert a daily close Series into the quote dict used by the UI."""
    last = safe_round(s.iloc[-1])
    prev = safe_round(s.iloc[-2])
    week_base = safe_round(s.iloc[-6]) if len(s) >= 6 else prev
    return {
        "last":       last,
        "prev_close": prev,
        "change_pct": pct_change(last, prev),
        "week_pct":   pct_change(last, week_base),
        "updated":    s.index[-1].strftime("%Y-%m-%d"),
    }


def realized_vol_30d(s):
    """Annualized 30-trading-day realized volatility, %."""
    try:
        tail = s.tail(31)
        if len(tail) < 15:
            return None
        rets = tail.pct_change().dropna()
        return safe_round(float(rets.std()) * math.sqrt(252) * 100, 1)
    except Exception:
        return None


def trend_4w(s):
    """% change over the last ~20 trading days."""
    try:
        if len(s) < 21:
            return None
        return pct_change(safe_round(s.iloc[-1]), safe_round(s.iloc[-21]))
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# BIS POLICY RATES
# ═══════════════════════════════════════════════════════════════════════════

def fetch_bis_policy_rate(country):
    """
    Fetch the daily policy-rate series for a country from the BIS API.
    Returns {rate, as_of, direction} or None.
    Direction is computed: rate vs. its value ~60 business days earlier.
    """
    cc = ISO2[country]
    for endpoint in BIS_ENDPOINTS:
        url = endpoint.format(cc=cc)
        try:
            r = requests.get(url, timeout=25, headers={"Accept": "text/csv"})
            if r.status_code != 200 or not r.text.strip():
                continue
            df = pd.read_csv(io.StringIO(r.text))
            tcol = next((c for c in df.columns if c.upper() == "TIME_PERIOD"), None)
            vcol = next((c for c in df.columns if c.upper() == "OBS_VALUE"), None)
            if tcol is None or vcol is None:
                continue
            df = df[[tcol, vcol]].dropna().sort_values(tcol)
            if df.empty:
                continue
            rate  = safe_round(df[vcol].iloc[-1])
            as_of = str(df[tcol].iloc[-1])[:10]
            prior = safe_round(df[vcol].iloc[-61]) if len(df) >= 61 else safe_round(df[vcol].iloc[0])
            if rate is None:
                continue
            if prior is None or abs(rate - prior) < 0.01:
                direction = "hold"
            elif rate < prior:
                direction = "cutting"
            else:
                direction = "hiking"
            return {"rate": rate, "as_of": as_of, "direction": direction}
        except Exception as e:
            log.warning(f"  BIS endpoint failed for {country}: {e}")
    return None


def load_previous_output():
    try:
        with open(OUTPUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# IMF / WORLD BANK
# ═══════════════════════════════════════════════════════════════════════════

def fetch_imf_indicator(indicator_code, iso3_list, years=2):
    url = f"{IMF_BASE}/{indicator_code}"
    params = {"periods": ",".join(
        str(datetime.now().year + i) for i in range(-1, years)
    )}
    def _pull():
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json().get("values", {}).get(indicator_code, {})
        return {
            iso3: {yr: safe_round(v, 1) for yr, v in data[iso3].items() if v is not None}
            for iso3 in iso3_list if iso3 in data
        }
    return _retry(_pull, label=f"IMF {indicator_code}") or {}


def fetch_wb_latest(indicator, iso3_list, source=None):
    """Latest non-null value per country: {ISO3: {value, year}}.
    source: World Bank database id (e.g., 3 = Worldwide Governance Indicators —
    WGI codes like GE.EST are not in the default WDI database)."""
    cc = ";".join(iso3_list)
    url = f"{WB_BASE}/country/{cc}/indicator/{indicator}"
    params = {"format": "json", "mrnev": 1, "per_page": 60}
    if source is not None:
        params["source"] = source
    def _pull():
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        if len(payload) < 2 or not payload[1]:
            return None
        out = {}
        for row in payload[1]:
            iso3 = row.get("countryiso3code", "")
            if row.get("value") is not None and iso3 not in out:
                out[iso3] = {"value": safe_round(row["value"], 2), "year": row.get("date", "")}
        return out
    return _retry(_pull, label=f"WB {indicator}") or {}


def fetch_wb_series(indicator, iso3_list, years=11):
    """Last N annual values per country: {ISO3: [{year, value}, ...]} ascending."""
    cc = ";".join(iso3_list)
    url = f"{WB_BASE}/country/{cc}/indicator/{indicator}"
    params = {"format": "json", "mrv": years, "per_page": years * len(iso3_list) + 20}
    def _pull():
        r = requests.get(url, params=params, timeout=25)
        r.raise_for_status()
        payload = r.json()
        if len(payload) < 2 or not payload[1]:
            return None
        out = {}
        for row in payload[1]:
            iso3 = row.get("countryiso3code", "")
            if row.get("value") is None:
                continue
            out.setdefault(iso3, []).append(
                {"year": row.get("date", ""), "value": safe_round(row["value"], 2)}
            )
        for iso3 in out:
            out[iso3].sort(key=lambda x: x["year"])
        return out
    return _retry(_pull, label=f"WB {indicator} series") or {}


# ═══════════════════════════════════════════════════════════════════════════
# HIGHLIGHTS (rule-based, bilingual — generated strictly from fetched data)
# ═══════════════════════════════════════════════════════════════════════════

COUNTRY_ES = {
    "Brazil": "Brasil", "Mexico": "México", "Colombia": "Colombia",
    "Argentina": "Argentina", "Chile": "Chile", "Peru": "Perú",
}
COMMODITY_ES = {
    "Brent Crude": "Brent", "Copper": "cobre", "Gold": "oro",
    "Soybean": "soya", "Coffee": "café",
}


def build_highlights(equities, fx, commodities, policy_rates, prev_rates):
    en, es = [], []

    # Best / worst weekly equity performer
    perf = [(c, d["week_pct"]) for c, d in equities.items() if d.get("week_pct") is not None]
    if len(perf) >= 2:
        best = max(perf, key=lambda x: x[1])
        worst = min(perf, key=lambda x: x[1])
        en.append(f"{FLAGS[best[0]]} {best[0]} led Core 6 equities this week ({best[1]:+.1f}%); "
                  f"{worst[0]} lagged ({worst[1]:+.1f}%).")
        es.append(f"{FLAGS[best[0]]} {COUNTRY_ES[best[0]]} lideró las bolsas del Core 6 esta semana ({best[1]:+.1f}%); "
                  f"{COUNTRY_ES[worst[0]]} quedó rezagado ({worst[1]:+.1f}%).")

    # Largest FX move (USD/XXX up = local currency depreciated)
    moves = [(p, d["week_pct"], d["country"]) for p, d in fx.items() if d.get("week_pct") is not None]
    if moves:
        pair, chg, ctry = max(moves, key=lambda x: abs(x[1]))
        if abs(chg) >= 0.5:
            verb_en = "depreciated" if chg > 0 else "appreciated"
            verb_es = "se depreció" if chg > 0 else "se apreció"
            cur = pair.split("/")[1]
            en.append(f"{FLAGS[ctry]} The {cur} {verb_en} {abs(chg):.1f}% vs. USD, the week's largest currency move.")
            es.append(f"{FLAGS[ctry]} El {cur} {verb_es} {abs(chg):.1f}% frente al USD, el mayor movimiento cambiario de la semana.")

    # Policy rate changes since last snapshot
    for c, d in policy_rates.items():
        prev = (prev_rates or {}).get(c, {}).get("rate")
        cur = d.get("rate")
        if prev is not None and cur is not None and abs(cur - prev) >= 0.01:
            bp = int(round((cur - prev) * 100))
            verb_en = "cut" if bp < 0 else "raised"
            verb_es = "recortó" if bp < 0 else "subió"
            en.append(f"{FLAGS[c]} {d['bank']} {verb_en} its policy rate {abs(bp)} bp to {cur}%.")
            es.append(f"{FLAGS[c]} {d['bank']} {verb_es} su tasa de política {abs(bp)} pb, a {cur}%.")

    # Commodity moves ≥ 2% on the week
    for name, d in commodities.items():
        chg = d.get("week_pct")
        if chg is not None and abs(chg) >= 2:
            dir_en = "rose" if chg > 0 else "fell"
            dir_es = "subió" if chg > 0 else "cayó"
            en.append(f"{d['emoji']} {name} {dir_en} {abs(chg):.1f}% this week ({d['latam']}).")
            es.append(f"{d['emoji']} El {COMMODITY_ES.get(name, name)} {dir_es} {abs(chg):.1f}% esta semana ({d['latam']}).")

    return {"en": en[:5], "es": es[:5]}


# ═══════════════════════════════════════════════════════════════════════════
# HISTORY
# ═══════════════════════════════════════════════════════════════════════════

def update_history(report_date, equities, fx, policy_rates):
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []

    snapshot = {
        "date": report_date,
        "equities": {c: {"last": d.get("last"), "ytd_pct": d.get("ytd_pct")}
                     for c, d in equities.items()},
        "fx": {p: d.get("last") for p, d in fx.items()},
        "rates": {c: d.get("rate") for c, d in policy_rates.items()},
    }
    history = [h for h in history if h.get("date") != report_date]
    history.append(snapshot)
    history = history[-HISTORY_CAP:]

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    log.info(f"History updated: {len(history)} weekly snapshots.")
    return history


# ═══════════════════════════════════════════════════════════════════════════
# MAIN FETCH ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

def fetch_all():
    log.info("═══ DLAC LATAM Weekly Monitor — Data Fetch (v2) Starting ═══")
    now = datetime.now()
    report_date = now.strftime("%Y-%m-%d")
    previous = load_previous_output()

    output = {
        "meta": {
            "generated":      now.isoformat(),
            "version":        2,
            "report_date":    report_date,
            "report_week":    now.strftime("Week of %B %d, %Y"),
            "report_week_es": _week_es(now),
            "sources": [
                "Yahoo Finance (yfinance)",
                "BIS Data Portal (policy rates)",
                "IMF World Economic Outlook API",
                "World Bank Open Data API (FDI, WGI)",
            ],
        },
        "equities": {}, "fx": {}, "commodities": {},
        "macro": {}, "policy_rates": {}, "fdi": {},
        "governance": {}, "signals": {}, "highlights": {},
    }

    fx_series = {}   # keep raw series for volatility calc
    eq_series = {}

    # ── 1. EQUITIES (with fallback chains) ───────────────────────────────
    log.info("Fetching equity indexes...")
    for country, chain in EQUITY_SOURCES.items():
        entry = None
        for src in chain:
            log.info(f"  {country}: trying {src['ticker']}")
            s = fetch_yf_series(src["ticker"])
            if s is not None:
                quote = series_to_quote(s)
                quote["ytd_pct"] = fetch_yf_ytd(src["ticker"], quote["last"])
                entry = {
                    "index":  src["index"],
                    "flag":   FLAGS[country],
                    "ticker": src["ticker"],
                    "proxy":  src.get("proxy", False),
                    **quote,
                }
                eq_series[country] = s
                break
            time.sleep(0.5)
        if entry is None:
            log.error(f"  {country}: ALL equity sources failed")
            entry = {
                "index": chain[0]["index"], "flag": FLAGS[country],
                "ticker": chain[0]["ticker"], "proxy": False,
                "unavailable": True,
                "last": None, "prev_close": None, "change_pct": None,
                "week_pct": None, "ytd_pct": None, "updated": None,
            }
        output["equities"][country] = entry
        time.sleep(0.3)

    # ── 2. FX RATES ───────────────────────────────────────────────────────
    log.info("Fetching FX rates...")
    for pair, cfg in FX_TICKERS.items():
        s = fetch_yf_series(cfg["ticker"])
        if s is not None:
            quote = series_to_quote(s)
            quote["ytd_pct"] = fetch_yf_ytd(cfg["ticker"], quote["last"])
            fx_series[pair] = s
        else:
            quote = {"last": None, "prev_close": None, "change_pct": None,
                     "week_pct": None, "ytd_pct": None, "updated": None,
                     "unavailable": True}
        output["fx"][pair] = {
            "flag": FLAGS[cfg["country"]], "country": cfg["country"],
            "ticker": cfg["ticker"], **quote,
        }
        time.sleep(0.3)

    # ── 3. COMMODITIES ────────────────────────────────────────────────────
    log.info("Fetching commodity prices...")
    for name, cfg in COMMODITY_TICKERS.items():
        s = fetch_yf_series(cfg["ticker"])
        quote = series_to_quote(s) if s is not None else \
            {"last": None, "prev_close": None, "change_pct": None,
             "week_pct": None, "updated": None, "unavailable": True}
        if s is not None:
            quote["ytd_pct"] = fetch_yf_ytd(cfg["ticker"], quote["last"])
        output["commodities"][name] = {
            "unit": cfg["unit"], "emoji": cfg["emoji"], "latam": cfg["latam"],
            **quote,
        }
        time.sleep(0.3)

    # ── 4. IMF MACRO ──────────────────────────────────────────────────────
    log.info("Fetching IMF macro data...")
    iso3_list = list(ISO3.values())
    yr_now, yr_next = str(now.year), str(now.year + 1)
    gdp   = fetch_imf_indicator(IMF_INDICATORS["gdp_growth"], iso3_list)
    inf   = fetch_imf_indicator(IMF_INDICATORS["inflation"], iso3_list)
    unemp = fetch_imf_indicator(IMF_INDICATORS["unemployment"], iso3_list)
    for country, iso3 in ISO3.items():
        output["macro"][country] = {
            "iso3": iso3, "flag": FLAGS[country],
            "gdp_current":  (gdp.get(iso3) or {}).get(yr_now),
            "gdp_forecast": (gdp.get(iso3) or {}).get(yr_next),
            "cpi_current":  (inf.get(iso3) or {}).get(yr_now),
            "cpi_forecast": (inf.get(iso3) or {}).get(yr_next),
            "unemployment": (unemp.get(iso3) or {}).get(yr_now),
        }

    # ── 5. POLICY RATES (BIS API, carry-forward fallback) ────────────────
    log.info("Fetching policy rates from BIS...")
    prev_rates = previous.get("policy_rates", {})
    # Rate-change highlights only make sense against a previous v2 snapshot
    # (v1 rates were manually maintained and could be months stale).
    prev_rates_for_highlights = prev_rates if previous.get("meta", {}).get("version") == 2 else {}
    for country in COUNTRIES:
        bis = fetch_bis_policy_rate(country)
        info = CENTRAL_BANKS[country]
        if bis:
            # Age check: a series whose latest observation is months old is
            # not a current rate (e.g., Argentina — the BCRA abandoned a
            # single policy rate in 2025, so the BIS series ends there).
            aged = False
            try:
                aged = (now - datetime.strptime(bis["as_of"], "%Y-%m-%d")).days > 120
            except (ValueError, TypeError):
                pass
            output["policy_rates"][country] = {
                **bis, "bank": info["bank"], "target": info["target"],
                "flag": FLAGS[country], "stale": aged,
                "source": "BIS (aged observation)" if aged else "BIS",
            }
            log.info(f"  {country}: {bis['rate']}% (as of {bis['as_of']}, {bis['direction']})"
                     + ("  [AGED — flagged stale]" if aged else ""))
        else:
            prev = prev_rates.get(country, {})
            output["policy_rates"][country] = {
                "rate": prev.get("rate"), "as_of": prev.get("as_of"),
                "direction": prev.get("direction", "hold"),
                "bank": info["bank"], "target": info["target"],
                "flag": FLAGS[country], "stale": True,
                "source": "carried forward (BIS unavailable)",
            }
            log.warning(f"  {country}: BIS unavailable — carried forward "
                        f"{prev.get('rate')}% as of {prev.get('as_of')} (flagged stale)")
        time.sleep(0.5)

    # ── 6. FDI & GOVERNANCE (World Bank) ─────────────────────────────────
    log.info("Fetching FDI flows and governance indicators (World Bank)...")
    fdi_series = fetch_wb_series(WB_FDI_USD, iso3_list, years=11)
    fdi_gdp    = fetch_wb_latest(WB_FDI_GDP, iso3_list)
    for country, iso3 in ISO3.items():
        series = fdi_series.get(iso3, [])
        latest = series[-1] if series else None
        output["fdi"][country] = {
            "flag": FLAGS[country],
            "latest_usd_bn": safe_round(latest["value"] / 1e9, 1) if latest else None,
            "latest_year":   latest["year"] if latest else None,
            "pct_gdp":       (fdi_gdp.get(iso3) or {}).get("value"),
            "pct_gdp_year":  (fdi_gdp.get(iso3) or {}).get("year"),
            "series": [{"year": p["year"], "usd_bn": safe_round(p["value"] / 1e9, 1)}
                       for p in series],
        }

    wgi = {key: fetch_wb_latest(code, iso3_list, source=3) for key, code in WB_WGI.items()}
    for country, iso3 in ISO3.items():
        output["governance"][country] = {"flag": FLAGS[country]}
        for key in WB_WGI:
            rec = wgi[key].get(iso3) or {}
            output["governance"][country][key] = rec.get("value")
            output["governance"][country][key + "_year"] = rec.get("year")

    # ── 7. COMPUTED SIGNALS ───────────────────────────────────────────────
    log.info("Computing market signals...")
    for country in COUNTRIES:
        pair = f"USD/{ {'Brazil':'BRL','Mexico':'MXN','Colombia':'COP','Argentina':'ARS','Chile':'CLP','Peru':'PEN'}[country] }"
        fxs = fx_series.get(pair)
        eqs = eq_series.get(country)
        vol = realized_vol_30d(fxs) if fxs is not None else None
        output["signals"][country] = {
            "flag": FLAGS[country],
            "fx_vol_30d": vol,
            "fx_vol_band": None if vol is None else ("low" if vol < 8 else "medium" if vol < 15 else "high"),
            "fx_trend_4w": trend_4w(fxs) if fxs is not None else None,
            "equity_trend_4w": trend_4w(eqs) if eqs is not None else None,
            "policy_direction": output["policy_rates"][country].get("direction"),
        }
    output["signals_methodology"] = {
        "en": ("Computed from market data; no analyst judgment. FX volatility: 30-trading-day "
               "realized volatility of daily returns, annualized. Bands: <8% low, 8–15% medium, "
               ">15% high. Trends: % change over the last 20 trading days. Policy direction: "
               "latest BIS policy rate vs. ~3 months earlier."),
        "es": ("Calculado a partir de datos de mercado; sin juicio de analista. Volatilidad FX: "
               "volatilidad realizada de 30 días hábiles de retornos diarios, anualizada. Bandas: "
               "<8% baja, 8–15% media, >15% alta. Tendencias: variación % en los últimos 20 días "
               "hábiles. Dirección de política: última tasa BIS vs. ~3 meses antes."),
    }

    # ── 8. HIGHLIGHTS ─────────────────────────────────────────────────────
    output["highlights"] = build_highlights(
        output["equities"], output["fx"], output["commodities"],
        output["policy_rates"], prev_rates_for_highlights,
    )

    # ── 9. WRITE OUTPUTS ─────────────────────────────────────────────────
    for path in (OUTPUT_FILE, DOCS_OUTPUT):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"═══ Data written to {OUTPUT_FILE} and {DOCS_OUTPUT} ═══")

    update_history(report_date, output["equities"], output["fx"], output["policy_rates"])

    _print_summary(output)
    _validate(output)
    return output


def _week_es(dt):
    months_es = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"Semana del {dt.day} de {months_es[dt.month]} de {dt.year}"


def _print_summary(data):
    print("\n" + "═" * 60)
    print("  DLAC LATAM Weekly Monitor — Fetch Summary (v2)")
    print("═" * 60)
    print(f"  Generated : {data['meta']['generated'][:19]}")
    print(f"  Report    : {data['meta']['report_week']}")
    print("\n  EQUITY INDEXES")
    for c, d in data["equities"].items():
        tag = " [ETF proxy]" if d.get("proxy") else (" [UNAVAILABLE]" if d.get("unavailable") else "")
        print(f"  {d['flag']} {c:<11} {d['index'][:28]:<28} {str(d.get('last') or 'N/A'):>12}{tag}")
    print("\n  POLICY RATES (BIS)")
    for c, d in data["policy_rates"].items():
        tag = " [STALE — carried forward]" if d.get("stale") else ""
        print(f"  {d['flag']} {c:<11} {str(d.get('rate'))+'%':>8}  as of {d.get('as_of')}{tag}")
    if data["highlights"].get("en"):
        print("\n  HIGHLIGHTS")
        for h in data["highlights"]["en"]:
            print(f"  • {h}")
    print("═" * 60 + "\n")


def _validate(data):
    """Emit warnings to the GitHub Actions summary; exit non-zero only on catastrophe."""
    problems = []
    for c, d in data["equities"].items():
        if d.get("last") is None:
            problems.append(f"Equity feed unavailable: {c}")
    for p, d in data["fx"].items():
        if d.get("last") is None:
            problems.append(f"FX feed unavailable: {p}")
    for c, d in data["policy_rates"].items():
        if d.get("stale"):
            problems.append(f"Policy rate stale ({d.get('source', 'unknown')}): {c}")
        if d.get("rate") is None:
            problems.append(f"Policy rate missing entirely: {c}")
    for c, d in data.get("governance", {}).items():
        missing = [k for k in WB_WGI if d.get(k) is None]
        if missing:
            problems.append(f"Governance indicators missing for {c}: {len(missing)}/{len(WB_WGI)}")
    for c, d in data.get("fdi", {}).items():
        if d.get("latest_usd_bn") is None:
            problems.append(f"FDI data missing: {c}")

    import os
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## DLAC Monitor — data quality\n\n")
            if problems:
                f.write("| Issue |\n|---|\n")
                for p in problems:
                    f.write(f"| ⚠️ {p} |\n")
            else:
                f.write("✅ All series fetched successfully.\n")

    for p in problems:
        log.warning(f"VALIDATION: {p}")

    fx_ok = sum(1 for d in data["fx"].values() if d.get("last") is not None)
    eq_ok = sum(1 for d in data["equities"].values() if d.get("last") is not None)
    if fx_ok == 0 and eq_ok == 0:
        log.error("Catastrophic fetch failure: no market data at all. Failing the run.")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    fetch_all()
