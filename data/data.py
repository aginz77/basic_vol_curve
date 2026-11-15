"""Fetch option chains and IV-by-delta surfaces from Massive (formerly Polygon.io).

Set your API key in the environment before use:
    export MASSIVE_API_KEY=your_key
"""

import datetime as dt
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "https://api.massive.com"


DEFAULT_DELTAS = [0.15, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50,
                  0.55, 0.60, 0.65, 0.70, 0.75, 0.85]

# Target tenors in calendar days; the closest listed expiry is used for each.
DEFAULT_TENORS = {"1W": 7, "2W": 14, "1M": 30, "2M": 60, "3M": 91,
                  "6M": 182, "9M": 273, "1Y": 365}


class MassiveError(RuntimeError):
    pass


def _resolve_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise MassiveError("Set MASSIVE_API_KEY or pass api_key.")
    return key


def _get(url: str, api_key: str, timeout: float = 20.0, retries: int = 1) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise MassiveError(f"HTTP {e.code}: {body[:500]}") from None
        except (TimeoutError, urllib.error.URLError):
            if attempt == retries:
                raise
    if payload.get("status") == "ERROR":
        raise MassiveError(payload.get("error") or payload.get("message") or str(payload))
    return payload


def _get_all(path: str, params: dict, api_key: str) -> list[dict]:
    """GET a Massive list endpoint, following next_url pagination."""
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    results = []
    while url:
        payload = _get(url, api_key)
        results.extend(payload.get("results", []))
        url = payload.get("next_url")
    return results


def _flatten(raw: dict) -> dict:
    """Flatten one snapshot result into a single-level contract dict."""
    details = raw.get("details", {})
    quote = raw.get("last_quote", {})
    trade = raw.get("last_trade", {})
    greeks = raw.get("greeks", {})
    day = raw.get("day", {})
    underlying = raw.get("underlying_asset", {})
    return {
        "ticker": details.get("ticker"),
        "expiration": details.get("expiration_date"),
        "strike": details.get("strike_price"),
        "type": details.get("contract_type"),  # "call" / "put"
        "bid": quote.get("bid"),
        "bid_size": quote.get("bid_size"),
        "ask": quote.get("ask"),
        "ask_size": quote.get("ask_size"),
        "mid": quote.get("midpoint"),
        "last": trade.get("price"),
        "close": day.get("close"),  # day's last trade price; available without a quotes plan
        "volume": day.get("volume"),
        "open_interest": raw.get("open_interest"),
        "implied_volatility": raw.get("implied_volatility"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "underlying_price": underlying.get("price"),
    }


def get_options_chain(
    symbol: str = "SPY",
    expiration_date: str | None = None,
    contract_type: str | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Return the current option chain snapshot for `symbol` (all pages).

    Optional filters: expiration_date ("YYYY-MM-DD"), contract_type ("call"/"put"),
    strike_min / strike_max.
    """
    params = {"limit": 250}
    if expiration_date:
        params["expiration_date"] = expiration_date
    if contract_type:
        params["contract_type"] = contract_type
    if strike_min is not None:
        params["strike_price.gte"] = round(strike_min, 2)
    if strike_max is not None:
        params["strike_price.lte"] = round(strike_max, 2)
    raw = _get_all(f"/v3/snapshot/options/{symbol}", params, _resolve_key(api_key))
    return [_flatten(r) for r in raw]


def get_spot(symbol: str, api_key: str | None = None) -> float:
    """Previous session's close for the underlying (1 request).

    Note: on a free stocks plan this endpoint is capped at 5 requests/minute.
    """
    payload = _get(f"{BASE_URL}/v2/aggs/ticker/{symbol}/prev", _resolve_key(api_key))
    results = payload.get("results") or []
    if not results:
        raise MassiveError(f"No price data for {symbol}")
    return results[0]["c"]


def estimate_spot(symbol: str, api_key: str | None = None, min_days: int = 20) -> float:
    """Estimate the underlying price from options only (usually 1-2 requests).

    Takes the first expiry at least `min_days` out and interpolates the strike
    where call delta crosses 0.5, which sits within a fraction of a percent of
    spot for a ~1-month expiry. Avoids the rate-limited stocks endpoint.
    """
    api_key = _resolve_key(api_key)
    start = (dt.date.today() + dt.timedelta(days=min_days)).isoformat()
    params = {"contract_type": "call", "expiration_date.gte": start,
              "sort": "expiration_date", "order": "asc", "limit": 250}
    url = f"{BASE_URL}/v3/snapshot/options/{symbol}?{urllib.parse.urlencode(params)}"
    expiry, chain = None, []
    while url:
        payload = _get(url, api_key)
        rows = [_flatten(r) for r in payload.get("results", [])]
        if not rows:
            break
        expiry = expiry or rows[0]["expiration"]
        chain += [r for r in rows if r["expiration"] == expiry]
        pts = sorted((c["strike"], c["delta"]) for c in chain if c["delta"] is not None)
        for (k1, d1), (k2, d2) in zip(pts, pts[1:]):
            if d1 >= 0.5 >= d2 and d1 != d2:
                return round(k1 + (d1 - 0.5) / (d1 - d2) * (k2 - k1), 2)
        if rows[-1]["expiration"] != expiry:  # moved past the first expiry
            break
        url = payload.get("next_url")
    raise MassiveError(f"Could not estimate a price for {symbol} from its options")


def get_atm_calls(symbol: str, spot: float, api_key: str | None = None) -> list[dict]:
    """Calls with strikes near spot, across every expiry (usually 1-2 requests).

    Every expiry lists strikes near the money, so this finds all expirations
    (and their ATM IVs) without downloading the whole chain.
    """
    half_width = max(0.006 * spot, 2.5)
    return get_options_chain(symbol, contract_type="call", strike_min=spot - half_width,
                             strike_max=spot + half_width, api_key=api_key)


def _option_delta(call_delta: float) -> tuple[str, float]:
    """OTM convention: call delta <= 0.5 -> that call; > 0.5 -> put with delta (d - 1)."""
    return ("call", call_delta) if call_delta <= 0.5 else ("put", call_delta - 1)


def iv_by_delta(chain: list[dict], deltas=DEFAULT_DELTAS) -> list[dict]:
    """For each target (call-equivalent) delta, return the closest OTM contract.

    Targets <= 0.5 use calls; targets > 0.5 use puts with delta = target - 1
    (e.g. 0.70 -> the -0.30 delta put). Expects a single expiry's calls and puts;
    contracts without IV/greeks are skipped. Each row has `target_delta` (call
    terms) and `delta_error` (|contract delta - wanted option delta|).
    """
    priced = [c for c in chain if c["implied_volatility"] is not None and c["delta"] is not None]
    rows = []
    for d in deltas:
        kind, want = _option_delta(d)
        side = [c for c in priced if c["type"] == kind]
        if not side:
            rows.append({"target_delta": d, "type": kind, "implied_volatility": None,
                         "delta": None, "delta_error": None})
            continue
        best = min(side, key=lambda c: abs(c["delta"] - want))
        rows.append({"target_delta": d, **best, "delta_error": abs(best["delta"] - want)})
    return rows


def pick_expirations(expirations, tenors=DEFAULT_TENORS, today=None) -> dict:
    """Map each tenor label to the listed expiry closest to it (duplicates dropped)."""
    today = today or dt.date.today()
    dte = {e: (dt.date.fromisoformat(e) - today).days for e in expirations}
    dte = {e: d for e, d in dte.items() if d > 0}  # skip same-day expiries
    if not dte:
        return {}
    picked = {}
    for label, days in tenors.items():
        best = min(dte, key=lambda e: abs(dte[e] - days))
        if best not in picked.values():
            picked[label] = best
    return picked


def _strike_bounds(spot: float, days: int, vol_guess: float, z: float = 2.0):
    """Strike range that should contain call deltas ~0.02-0.98 under a lognormal model.

    vol_guess is padded above ATM IV to allow for skew; if the range still misses
    the delta targets, the caller refetches the expiry unfiltered.
    """
    width = z * vol_guess * math.sqrt(max(days, 1) / 365)
    return spot * math.exp(-width), spot * math.exp(width)


def _fetch_side(symbol, expiry, kind, lo, hi, wanted, tol, api_key):
    """Fetch one side (calls or puts) within [lo, hi]; refetch unfiltered if the
    window doesn't span the wanted deltas."""
    if not wanted:
        return []
    chain = get_options_chain(symbol, expiry, kind, lo, hi, api_key)
    have = [c["delta"] for c in chain if c["delta"] is not None]
    if not have or min(have) > min(wanted) + tol or max(have) < max(wanted) - tol:
        chain = get_options_chain(symbol, expiry, kind, api_key=api_key)  # fallback
    return chain


def _fetch_expiry(symbol, expiry, spot, vol_guess, deltas, tol, api_key):
    """OTM calls (strikes from just below spot up) + OTM puts (just above spot down)."""
    days = (dt.date.fromisoformat(expiry) - dt.date.today()).days
    lo, hi = _strike_bounds(spot, days, vol_guess)
    overlap = math.log(hi / spot) * 0.2  # small margin either side of spot
    call_wanted = [_option_delta(d)[1] for d in deltas if d <= 0.5]
    put_wanted = [_option_delta(d)[1] for d in deltas if d > 0.5]
    calls = _fetch_side(symbol, expiry, "call", spot * math.exp(-overlap), hi,
                        call_wanted, tol, api_key)
    puts = _fetch_side(symbol, expiry, "put", lo, spot * math.exp(overlap),
                       put_wanted, tol, api_key)
    return calls + puts


def iv_surface(symbol="SPY", deltas=DEFAULT_DELTAS, tenors=DEFAULT_TENORS,
               max_delta_error=0.03, api_key=None) -> dict:
    """IV by delta across expirations, using OTM options (see iv_by_delta).

    Returns {"symbol", "spot", "deltas", "expiries": {expiry: {"tenor", "rows"}}}.
    "spot" is estimated from the option chain (see estimate_spot).
    A row whose nearest contract is more than `max_delta_error` from its target
    delta gets implied_volatility=None.

    Requests: 1 (spot) + 1-2 (expiry list) + ~2 per expiry (calls + puts), in parallel.
    """
    symbol = symbol.upper().strip()
    api_key = _resolve_key(api_key)
    spot = estimate_spot(symbol, api_key)
    atm = get_atm_calls(symbol, spot, api_key)
    picked = pick_expirations({c["expiration"] for c in atm}, tenors)
    if not picked:
        raise MassiveError(f"No option expirations found for {symbol}")

    # Per-expiry ATM IV sizes the strike window for that expiry's fetch.
    atm_iv = {}
    for c in atm:
        if c["implied_volatility"]:
            atm_iv[c["expiration"]] = max(atm_iv.get(c["expiration"], 0), c["implied_volatility"])

    def fetch(expiry):
        vol_guess = max(0.15, 1.5 * atm_iv.get(expiry, 0.6))
        return _fetch_expiry(symbol, expiry, spot, vol_guess, deltas, max_delta_error, api_key)

    with ThreadPoolExecutor(max_workers=8) as pool:
        chains = dict(zip(picked.values(), pool.map(fetch, picked.values())))

    expiries = {}
    for label, expiry in picked.items():
        rows = iv_by_delta(chains[expiry], deltas)
        for r in rows:
            if r["delta_error"] is not None and r["delta_error"] > max_delta_error:
                r["implied_volatility"] = None
        expiries[expiry] = {"tenor": label, "rows": rows}
    return {"symbol": symbol, "spot": spot, "deltas": list(deltas), "expiries": expiries}


if __name__ == "__main__":
    import sys
    surface = iv_surface(sys.argv[1] if len(sys.argv) > 1 else "SPY")
    exps = surface["expiries"]

    print(f"{surface['symbol']} OTM IV by call-equivalent delta "
          f"(<=0.5 calls, >0.5 puts; spot ≈ {surface['spot']})\n")
    print(f"{'Δ':>5} " + " ".join(f"{exps[e]['tenor']:>10}" for e in exps))
    print(f"{'':>5} " + " ".join(f"{e[2:]:>10}" for e in exps))
    for i, d in enumerate(surface["deltas"]):
        cells = []
        for e in exps:
            rows = exps[e]["rows"]
            iv = rows[i]["implied_volatility"] if rows else None
            cells.append(f"{iv:>10.2%}" if iv is not None else f"{'-':>10}")
        print(f"{d:>5.2f} " + " ".join(cells))
