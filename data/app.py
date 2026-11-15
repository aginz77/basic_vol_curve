"""Minimal Flask prototype: enter a ticker, see the forward-implied IV surface.

Left:  3D IV surface (delta x days to expiry).
Right: IV by delta for a chosen expiry; IV by expiry for a chosen delta.

Local dev:   python app.py   then open http://127.0.0.1:5001  (auth disabled)
Production:  gunicorn behind Cloudflare Tunnel + Access (see DEPLOY.md)
"""

import re

from flask import Flask, g, render_template_string, request

from auth import verify_access
from data import MassiveError
from vol import forward_iv_surface

app = Flask(__name__)
app.before_request(verify_access)

PAGE = """<!doctype html>
<title>Implied Vol</title>
<link rel="icon" type="image/png" href="{{ url_for('static', filename='favicon.png') }}">
<link rel="apple-touch-icon" href="{{ url_for('static', filename='apple-touch-icon.png') }}">
{% if user_email %}<p>Signed in as {{ user_email }} · <a href="/cdn-cgi/access/logout">Log out</a></p>{% endif %}
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<form>
  <input name="ticker" value="{{ ticker }}" placeholder="Ticker">
  <button>Go</button>
</form>
{% if error %}<p>{{ error }}</p>{% endif %}
{% if data %}
<div style="display:flex">
  <div style="flex:1">
    <div id="surface" style="height:700px"></div>
  </div>
  <div style="flex:1">
    <label>Expiry <select id="expiry-select"></select></label>
    <div id="by-delta" style="height:340px"></div>
    <label>Delta <select id="delta-select"></select></label>
    <div id="by-expiry" style="height:340px"></div>
  </div>
</div>
<script>
const data = {{ data|tojson }};
const deltas = data.deltas;
const exps = data.expiries;
const days = exps.map(e => e.days);
const expLabels = exps.map(e => `${e.tenor} ${e.expiry}`);

// --- 3D surface: x = delta, y = days to expiry, z (vertical) = IV ---
const z = exps.map(e => e.ivs);  // rows = expiries, columns = deltas
const gridLine = (x, y, zz) => ({type: "scatter3d", mode: "lines", x, y, z: zz,
  line: {color: "black", width: 2}, hoverinfo: "skip", showlegend: false});
const traces = [{
  type: "surface", x: deltas, y: days, z,
  // Colour by IV: mostly green, medium bumps toward yellow, the biggest spikes red.
  colorscale: [[0, "#1a9e1a"], [0.5, "#4cd137"], [0.75, "#f5e130"], [1, "#e0281e"]],
  showscale: false,
  lighting: {ambient: 0.6, diffuse: 0.8, specular: 0.1},
  hovertemplate: "Δ %{x}<br>%{y} days<br>IV %{z:.2f}%<extra></extra>",
}];
exps.forEach(e => traces.push(gridLine(deltas, deltas.map(() => e.days), e.ivs)));
deltas.forEach((d, i) => traces.push(gridLine(days.map(() => d), days, exps.map(e => e.ivs[i]))));
Plotly.newPlot("surface", traces, {
  title: `${data.symbol} IV surface (spot ≈ ${data.spot})`,
  scene: {
    aspectmode: "cube",
    // Default view: from the front (delta left-to-right, expiry going back), slightly above.
    camera: {eye: {x: -0.35, y: -2.1, z: 0.35}, up: {x: 0, y: 0, z: 1}},
    xaxis: {title: "Delta (call-equiv.)"},
    yaxis: {title: "Expiry", tickvals: days, ticktext: exps.map(e => e.tenor)},
    zaxis: {title: "IV (%)"},
  },
});

// --- IV by delta for one expiry ---
const expSel = document.getElementById("expiry-select");
exps.forEach((e, i) => expSel.add(new Option(`${expLabels[i]} (F ${e.forward.toFixed(2)})`, i)));
function drawByDelta() {
  const e = exps[expSel.value];
  Plotly.react("by-delta", [{x: deltas, y: e.ivs, mode: "lines+markers"}], {
    title: `IV by delta: ${expLabels[expSel.value]}`,
    xaxis: {title: "Delta (call-equiv.; <=0.5 calls, >0.5 puts)"},
    yaxis: {title: "IV (%)"},
  });
}
expSel.onchange = drawByDelta;

// --- IV by expiry for one delta ---
const deltaSel = document.getElementById("delta-select");
deltas.forEach((d, i) => deltaSel.add(new Option(d.toFixed(2), i)));
deltaSel.value = deltas.indexOf(0.5);
function drawByExpiry() {
  const i = deltaSel.value;
  Plotly.react("by-expiry", [{x: days, y: exps.map(e => e.ivs[i]), mode: "lines+markers",
                              text: expLabels, hovertemplate: "%{text}<br>IV %{y:.2f}%<extra></extra>"}], {
    title: `IV by expiry: delta ${deltas[i].toFixed(2)}`,
    xaxis: {title: "Days to expiry", tickvals: days, ticktext: exps.map(e => e.tenor)},
    yaxis: {title: "IV (%)"},
  });
}
deltaSel.onchange = drawByExpiry;

drawByDelta();
drawByExpiry();
</script>
{% endif %}
"""


def to_payload(surface) -> dict:
    """Trim the surface to what the page needs; IVs in percent, None for gaps."""
    return {
        "symbol": surface["symbol"],
        "spot": surface["spot"],
        "deltas": surface["deltas"],
        "expiries": [
            {"expiry": e["expiry"], "tenor": e["tenor"], "days": e["days"],
             "forward": e["forward"],
             "ivs": [round(r["iv"] * 100, 3) if r["iv"] is not None else None
                     for r in e["rows"]]}
            for e in surface["expiries"]
        ],
    }


@app.route("/")
def index():
    ticker = request.args.get("ticker", "").strip().upper()
    data = error = None
    if ticker:
        if not re.fullmatch(r"[A-Z][A-Z.]{0,9}", ticker):
            error = "Invalid ticker."
        else:
            try:
                data = to_payload(forward_iv_surface(ticker))
            except MassiveError as e:
                error = f"Massive error: {e}"
            except Exception as e:  # network errors etc.; prototype-level handling
                error = f"Error: {e}"
    return render_template_string(PAGE, ticker=ticker, data=data, error=error,
                                  user_email=g.get("user_email"))


if __name__ == "__main__":
    # Local dev only: no Cloudflare in front, so skip the Access check.
    # Port 5000 is taken by macOS AirPlay Receiver (returns 403), so use 5001.
    app.config["AUTH_DISABLED"] = True
    app.run(port=5001, debug=True)
