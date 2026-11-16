"""Forward-consistent IV curve for a single tenor.

Steps:
  1. Strike K0 closest to spot (with both a call and a put price).
  2. Implied forward from put-call parity: F = K0 + e^{rT} (C - P).
  3. IV for every option from Black-76 on F, so calls and puts share one forward.
  4. OTM selection by delta (calls for <= 0.5, puts with delta d-1 for > 0.5);
     the 0.5 point averages the call and put IV at the same strike.
"""

import datetime as dt
import math
from concurrent.futures import ThreadPoolExecutor

from data import (DEFAULT_DELTAS, DEFAULT_TENORS, BASE_URL, MassiveError, _fetch_expiry, _get,
                  _resolve_key, estimate_spot, get_atm_calls, pick_expirations)

# Treasury yield fields and their maturities in days.
_YIELD_TENORS = [("yield_1_month", 30), ("yield_3_month", 91), ("yield_1_year", 365),
                 ("yield_2_year", 730), ("yield_5_year", 1826), ("yield_10_year", 3652)]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_price(F, K, T, r, sigma, kind) -> float:
    """Black-76 price of a European option on forward F, discounted at r."""
    df = math.exp(-r * T)
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind == "call":
        return df * (F * _norm_cdf(d1) - K * _norm_cdf(d2))
    return df * (K * _norm_cdf(-d2) - F * _norm_cdf(-d1))


def black_delta(F, K, T, sigma, kind) -> float:
    """Forward delta: N(d1) for calls, N(d1) - 1 for puts."""
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    return _norm_cdf(d1) - (0 if kind == "call" else 1)


def implied_vol(price, F, K, T, r, kind, lo=1e-4, hi=5.0, tol=1e-7) -> float | None:
    """Bisection IV solve; None if the price is outside the no-arbitrage range."""
    if price is None or price <= 0:
        return None
    if not black_price(F, K, T, r, lo, kind) < price < black_price(F, K, T, r, hi, kind):
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if black_price(F, K, T, r, mid, kind) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def get_treasury_curve(api_key: str | None = None) -> list[tuple[int, float]]:
    """Latest Treasury yields as [(days, yield %)], shortest first (1 request)."""
    payload = _get(f"{BASE_URL}/fed/v1/treasury-yields?limit=1&sort=date.desc",
                   _resolve_key(api_key))
    row = payload["results"][0]
    return [(d, row[f]) for f, d in _YIELD_TENORS if row.get(f) is not None]


def rate_for_days(curve: list[tuple[int, float]], days: int) -> float:
    """Continuously compounded rate for `days`, linearly interpolated on the curve."""
    if days <= curve[0][0]:
        y = curve[0][1]
    elif days >= curve[-1][0]:
        y = curve[-1][1]
    else:
        for (d1, y1), (d2, y2) in zip(curve, curve[1:]):
            if d1 <= days <= d2:
                y = y1 + (y2 - y1) * (days - d1) / (d2 - d1)
                break
    return math.log(1 + y / 100)


def _price(c: dict) -> tuple[float | None, str]:
    """Mid if quotes are available, else the day's last trade (close)."""
    if c.get("mid"):
        return c["mid"], "mid"
    if c.get("bid") and c.get("ask"):
        return (c["bid"] + c["ask"]) / 2, "mid"
    return c.get("close"), "close"


MIN_PRICE = 0.05  # below this, tick size alone swings IV wildly


def usable_contracts(chain: list[dict]) -> list[dict]:
    """Drop contracts whose last-trade price is likely stale or too small to trust.

    Without a quotes plan prices are last trades, so a contract that did not trade in
    the latest session (or traded on an older day than the rest of the chain) was
    priced when the underlying was elsewhere.
    """
    traded = [c for c in chain if (c.get("volume") or 0) > 0
              and (_price(c)[0] or 0) >= MIN_PRICE]
    if any(c.get("mid") for c in traded):
        return traded  # real quotes: freshness is not an issue
    stamps = [c["day_updated"] for c in traded if c.get("day_updated")]
    if not stamps:
        return traded
    latest = dt.datetime.fromtimestamp(max(stamps) / 1e9, dt.timezone.utc).date()
    return [c for c in traded if not c.get("day_updated") or
            dt.datetime.fromtimestamp(c["day_updated"] / 1e9, dt.timezone.utc).date() == latest]


def clean_rows(rows: list[dict], rel_tol=0.25, abs_tol=0.05, edge_rel_tol=0.4) -> list[dict]:
    """Remove spikes from one expiry's IV-by-delta curve, then fill interior gaps.

    A point is a spike if it is further than max(rel_tol * expected, abs_tol) from the
    line through its neighbours (edges: extrapolated from the two inner points, with the
    looser edge_rel_tol since wings legitimately curve up). The worst spike is removed
    first and the check repeats. Interior gaps are then filled linearly in delta and
    flagged with filled=True; the ends of the curve are never extrapolated.
    """
    x = [r["target_delta"] for r in rows]
    iv = [r["iv"] for r in rows]

    def expected(i, have):
        left = [j for j in have if j < i]
        right = [j for j in have if j > i]
        if left and right:
            a, b = left[-1], right[0]
        elif len(right) >= 2:
            a, b = right[0], right[1]
        elif len(left) >= 2:
            a, b = left[-2], left[-1]
        else:
            return None, None
        e = iv[a] + (iv[b] - iv[a]) * (x[i] - x[a]) / (x[b] - x[a])
        return e, (rel_tol if left and right else edge_rel_tol)

    for _ in range(len(rows)):
        have = [i for i, v in enumerate(iv) if v is not None]
        if len(have) < 4:
            break
        worst, worst_excess = None, 0.0
        for i in have:
            e, tol = expected(i, [j for j in have if j != i])
            if e is None or e <= 0:
                continue
            excess = abs(iv[i] - e) - max(tol * e, abs_tol)
            if excess > worst_excess:
                worst, worst_excess = i, excess
        if worst is None:
            break
        rows[worst] = {**rows[worst], "iv": None, "dropped_iv": iv[worst]}
        iv[worst] = None

    have = [i for i, v in enumerate(iv) if v is not None]
    for i in range(len(rows)):
        if iv[i] is None and have and have[0] < i < have[-1]:
            a = max(j for j in have if j < i)
            b = min(j for j in have if j > i)
            w = (x[i] - x[a]) / (x[b] - x[a])
            sa, sb = rows[a]["strike"], rows[b]["strike"]
            rows[i] = {**rows[i], "iv": iv[a] + w * (iv[b] - iv[a]), "filled": True,
                       "strike": sa + w * (sb - sa) if sa and sb else rows[i]["strike"]}
    return rows


def implied_forward(chain: list[dict], spot: float, T: float, r: float) -> dict:
    """F = K0 + e^{rT} (C - P) at the strike K0 closest to spot with both prices."""
    calls = {c["strike"]: c for c in chain if c["type"] == "call" and _price(c)[0]}
    puts = {c["strike"]: c for c in chain if c["type"] == "put" and _price(c)[0]}
    common = set(calls) & set(puts)
    if not common:
        raise MassiveError("No strike has both a call and a put price")
    K0 = min(common, key=lambda k: abs(k - spot))
    C, src_c = _price(calls[K0])
    P, src_p = _price(puts[K0])
    F = K0 + math.exp(r * T) * (C - P)
    return {"forward": F, "K0": K0, "call_price": C, "put_price": P,
            "price_source": src_c if src_c == src_p else f"{src_c}/{src_p}"}


def expiry_curve(chain: list[dict], expiry: str, spot: float, r: float,
                 deltas=DEFAULT_DELTAS, max_delta_error=0.03) -> dict:
    """Forward-consistent IV by call-equivalent delta for one expiry's calls + puts."""
    days = (dt.date.fromisoformat(expiry) - dt.date.today()).days
    T = days / 365
    chain = usable_contracts(chain)
    fwd = implied_forward(chain, spot, T, r)
    F = fwd["forward"]

    # Our own IV and forward delta for every contract.
    priced = []
    for c in chain:
        price, _ = _price(c)
        iv = implied_vol(price, F, c["strike"], T, r, c["type"])
        if iv is not None:
            priced.append({**c, "price": price, "massive_iv": c["implied_volatility"],
                           "iv": iv, "fwd_delta": black_delta(F, c["strike"], T, iv, c["type"])})
    by_strike = {}
    for c in priced:
        by_strike.setdefault(c["strike"], {})[c["type"]] = c

    rows = []
    for d in deltas:
        row = {"target_delta": d, "iv": None, "strike": None, "delta": None}
        if d == 0.5:
            # Average call and put IV at the strike whose call delta is closest to 0.5.
            both = [s for s in by_strike.values() if "call" in s and "put" in s]
            if both:
                best = min(both, key=lambda s: abs(s["call"]["fwd_delta"] - 0.5))
                c, p = best["call"], best["put"]
                err = abs(c["fwd_delta"] - 0.5)
                row.update(type="avg", strike=c["strike"], delta=c["fwd_delta"],
                           call_iv=c["iv"], put_iv=p["iv"],
                           iv=(c["iv"] + p["iv"]) / 2 if err <= max_delta_error else None)
        else:
            kind, want = ("call", d) if d < 0.5 else ("put", d - 1)
            side = [c for c in priced if c["type"] == kind]
            row["type"] = kind
            if side:
                best = min(side, key=lambda c: abs(c["fwd_delta"] - want))
                err = abs(best["fwd_delta"] - want)
                row.update(strike=best["strike"], delta=best["fwd_delta"],
                           massive_iv=best["massive_iv"],
                           iv=best["iv"] if err <= max_delta_error else None)
        rows.append(row)

    return {"expiry": expiry, "days": days, "T": T, "rate": r, **fwd, "rows": clean_rows(rows)}


def forward_iv_surface(symbol="SPY", tenors=DEFAULT_TENORS, deltas=DEFAULT_DELTAS,
                       max_delta_error=0.03, api_key=None) -> dict:
    """Forward-consistent IV by delta for the expiry closest to each tenor.

    Each expiry gets its own rate (Treasury curve) and parity-implied forward.
    Returns {"symbol", "spot", "deltas", "expiries": [expiry_curve dicts + "tenor"]},
    sorted by expiry. Expiries where no forward could be implied are skipped.
    """
    symbol = symbol.upper().strip()
    api_key = _resolve_key(api_key)
    spot = estimate_spot(symbol, api_key)
    atm = get_atm_calls(symbol, spot, api_key)
    picked = pick_expirations({c["expiration"] for c in atm}, tenors)
    if not picked:
        raise MassiveError(f"No option expirations found for {symbol}")
    curve = get_treasury_curve(api_key)

    atm_iv = {}
    for c in atm:
        if c["implied_volatility"]:
            atm_iv[c["expiration"]] = max(atm_iv.get(c["expiration"], 0), c["implied_volatility"])

    def build(item):
        label, expiry = item
        vol_guess = max(0.15, 1.5 * atm_iv.get(expiry, 0.6))
        # Calls from just below spot up, puts from just above spot down: both sides
        # overlap around spot, which the parity strike and the 0.5 average need.
        chain = _fetch_expiry(symbol, expiry, spot, vol_guess, deltas, max_delta_error, api_key)
        days = (dt.date.fromisoformat(expiry) - dt.date.today()).days
        try:
            ec = expiry_curve(chain, expiry, spot, rate_for_days(curve, days),
                              deltas, max_delta_error)
        except MassiveError:
            return None
        # An expiry with fewer than half its points is too illiquid to plot.
        if sum(r["iv"] is not None for r in ec["rows"]) < len(deltas) / 2:
            return None
        return {"tenor": label, **ec}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for r in pool.map(build, picked.items()) if r]
    if not results:
        raise MassiveError(f"Could not imply a forward for any {symbol} expiry")
    results.sort(key=lambda e: e["expiry"])
    return {"symbol": symbol, "spot": spot, "deltas": list(deltas), "expiries": results}


def forward_iv_curve(symbol="SPY", tenor_days=30, **kwargs) -> dict:
    """Single-tenor version of forward_iv_surface."""
    surface = forward_iv_surface(symbol, {"T": tenor_days}, **kwargs)
    return {"symbol": surface["symbol"], "spot": surface["spot"], **surface["expiries"][0]}


if __name__ == "__main__":
    import sys
    sf = forward_iv_surface(sys.argv[1] if len(sys.argv) > 1 else "SPY")
    ex = sf["expiries"]
    print(f"{sf['symbol']} forward-implied OTM IV (spot ≈ {sf['spot']})\n")
    print(f"{'':>5} " + " ".join(f"{e['tenor']:>9}" for e in ex))
    print(f"{'F':>5} " + " ".join(f"{e['forward']:>9.2f}" for e in ex))
    print(f"{'r':>5} " + " ".join(f"{e['rate']:>9.3%}" for e in ex))
    for i, d in enumerate(sf["deltas"]):
        cells = [e["rows"][i]["iv"] for e in ex]
        print(f"{d:>5.2f} " + " ".join(f"{v:>9.2%}" if v is not None else f"{'-':>9}"
                                       for v in cells))
