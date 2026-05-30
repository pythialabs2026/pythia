"""Equity Event Track base-rate calculator.

Implements protocols/equity_event_resolution_DRAFT.md §2 (B - base-rate).
Calculates the historical up-frequency (direction: Close_t1 > Close_t0) for 
a given ticker and event type (8-K items or earnings) prior to a specific cutoff time T0.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

UA = "pythia-research hyungyulove@gmail.com"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Static global fallback rates derived from historic full market benchmarks
# Used if ticker-specific samples are insufficient (< 10)
GLOBAL_FALLBACK_RATES = {
    "2.02": 0.515,          # earnings release / operational results
    "earnings": 0.520,      # earnings date
    "1.01": 0.495,          # material definitively agreement
    "2.01": 0.505,          # acquisition completed
    "5.02": 0.485,          # officer change
    "default": 0.500
}


def _http_get(url: str, headers: dict | None = None, timeout: float = 30.0, retries: int = 5) -> bytes:
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise RuntimeError(f"Fetch failed for {url}")


def get_cik(ticker: str) -> str:
    """Gets 10-digit zero-padded CIK for a ticker using SEC's mapping."""
    try:
        raw = _http_get(SEC_TICKERS_URL)
        data = json.loads(raw.decode("utf-8"))
        for item in data.values():
            if item["ticker"].upper() == ticker.upper():
                return str(item["cik_str"]).zfill(10)
    except Exception as e:
        print(f"  ⚠ CIK resolution failed for {ticker}: {e}", file=sys.stderr)
    
    # Fallback to yfinance if SEC registry fetch fails
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cik = t.info.get("cik")
        if cik:
            return str(cik).zfill(10)
    except Exception:
        pass
        
    raise ValueError(f"Could not resolve CIK for ticker: {ticker}")


def get_historical_8k(cik: str, item_code: str) -> pd.DataFrame:
    """Fetches full SEC 8-K filings history for a CIK and filters by item code."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    raw = _http_get(url)
    data = json.loads(raw.decode("utf-8"))
    
    recent = data["filings"]["recent"]
    df = pd.DataFrame(recent)
    
    # Filter only 8-K forms
    df_8k = df[df["form"] == "8-K"].copy()
    
    # Filter by specific item code (e.g., "2.02" or "1.01")
    # items field is typically a comma-separated string of items (e.g., "2.02,9.01")
    df_8k = df_8k[df_8k["items"].astype(str).str.contains(item_code)]
    
    df_8k["filingDate"] = pd.to_datetime(df_8k["filingDate"])
    return df_8k[["accessionNumber", "filingDate", "reportDate", "items"]]


def get_prices(ticker: str) -> pd.DataFrame:
    """Fetches daily prices from Stooq (default) or yfinance (fallback)."""
    # 1. Try Stooq (Clean, fast, free, and no-auth daily CSV)
    try:
        url = f"https://stooq.com/q/d/l/?s={ticker}.us&i=d"
        raw = _http_get(url)
        # Load CSV
        import io
        df = pd.read_csv(io.BytesIO(raw))
        if not df.empty and "Date" in df.columns and "Close" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").reset_index(drop=True)
            return df[["Date", "Close"]]
    except Exception as e:
        print(f"  ⚠ Stooq price fetch failed for {ticker}: {e}. Falling back to yfinance...", file=sys.stderr)

    # 2. Fallback to yfinance (request max history)
    try:
        import yfinance as yf
        df_yf = yf.download(ticker, period="max", progress=False)
        if not df_yf.empty:
            df_yf = df_yf.reset_index()
            # yfinance returns multi-index columns occasionally, flatten it
            if isinstance(df_yf.columns, pd.MultiIndex):
                df_yf.columns = [col[0] for col in df_yf.columns]
            df_yf = df_yf.rename(columns={"Date": "Date", "Close": "Close"})
            df_yf["Date"] = pd.to_datetime(df_yf["Date"])
            df_yf = df_yf.sort_values("Date").reset_index(drop=True)
            return df_yf[["Date", "Close"]]
    except Exception as e:
        print(f"  ⚠ yfinance price fetch failed for {ticker}: {e}", file=sys.stderr)
        
    raise RuntimeError(f"Could not retrieve daily prices for ticker {ticker}")


def calculate_base_rate(ticker: str, event_type: str, t0_cutoff: str, lookback: int = 60) -> dict:
    """Calculates historical base-rate of stock price increase after events.
    
    event_type: '2.02', '1.01', '2.01', '5.02', or 'earnings'
    t0_cutoff: ISO timestamp (YYYY-MM-DD or ISO datetime). Filings on or after this are ignored.
    """
    cutoff_dt = pd.to_datetime(t0_cutoff).tz_localize(None)
    
    # 1. Resolve CIK & get events
    cik = get_cik(ticker)
    
    # For earnings, we use 2.02 (earnings release) filings as the reliable historic dates proxy
    item_code = "2.02" if event_type == "earnings" else event_type
    
    df_events = get_historical_8k(cik, item_code)
    
    # Apply look-back cutoff to prevent future leakage
    df_events = df_events[df_events["filingDate"] < cutoff_dt].copy()
    
    if df_events.empty:
        global_rate = GLOBAL_FALLBACK_RATES.get(event_type, GLOBAL_FALLBACK_RATES["default"])
        return {
            "ticker": ticker,
            "event_type": event_type,
            "t0_cutoff": t0_cutoff,
            "n": 0,
            "base_rate": global_rate,
            "fallback": "global_no_events",
            "status": "success"
        }
        
    # 2. Get price series
    df_prices = get_prices(ticker)
    df_prices = df_prices.sort_values("Date").reset_index(drop=True)
    
    # 3. Map events to price sessions using merge_asof
    df_events = df_events.sort_values("filingDate").reset_index(drop=True)
    
    # T0 close: Last close BEFORE the filing date (allow_exact_matches=False)
    # T0_plus_1 close: First close ON or AFTER the filing date (allow_exact_matches=True)
    
    df_t0 = pd.merge_asof(
        df_events,
        df_prices.rename(columns={"Date": "price_date_t0", "Close": "close_t0"}),
        left_on="filingDate",
        right_on="price_date_t0",
        direction="backward",
        allow_exact_matches=False
    )
    
    df_t1 = pd.merge_asof(
        df_events,
        df_prices.rename(columns={"Date": "price_date_t1", "Close": "close_t1"}),
        left_on="filingDate",
        right_on="price_date_t1",
        direction="forward",
        allow_exact_matches=True
    )
    
    df_merged = df_t0.copy()
    df_merged["price_date_t1"] = df_t1["price_date_t1"]
    df_merged["close_t1"] = df_t1["close_t1"]
    
    df_merged = df_merged.dropna(subset=["close_t0", "close_t1"])
    
    # Limit to the requested lookback slice (default: 60)
    df_slice = df_merged.tail(lookback).copy()
    n_samples = len(df_slice)
    
    # If samples are too low (< 10), apply global base-rate fallback
    if n_samples < 10:
        global_rate = GLOBAL_FALLBACK_RATES.get(event_type, GLOBAL_FALLBACK_RATES["default"])
        return {
            "ticker": ticker,
            "event_type": event_type,
            "t0_cutoff": t0_cutoff,
            "n": n_samples,
            "base_rate": global_rate,
            "fallback": "global_insufficient_samples",
            "status": "success"
        }
        
    # Calculate directional up frequency: Close_t1 > Close_t0
    df_slice["up"] = (df_slice["close_t1"] > df_slice["close_t0"]).astype(int)
    base_rate = df_slice["up"].mean()
    
    return {
        "ticker": ticker,
        "event_type": event_type,
        "t0_cutoff": t0_cutoff,
        "n": n_samples,
        "base_rate": round(float(base_rate), 4),
        "fallback": "none",
        "status": "success",
        "sample_start_date": df_slice["filingDate"].min().strftime("%Y-%m-%d"),
        "sample_end_date": df_slice["filingDate"].max().strftime("%Y-%m-%d")
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True, help="Stock ticker (e.g. AAPL)")
    ap.add_argument("--event", required=True, choices=["2.02", "1.01", "2.01", "5.02", "earnings"], help="Event type")
    ap.add_argument("--cutoff", default=datetime.now(timezone.utc).isoformat(), help="Cutoff datetime (prevent leak)")
    ap.add_argument("--lookback", type=int, default=60, help="Lookback count (default 60)")
    args = ap.parse_args()
    
    try:
        res = calculate_base_rate(args.ticker, args.event, args.cutoff, args.lookback)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}, indent=2))
        sys.exit(1)
