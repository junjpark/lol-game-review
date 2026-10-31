"""Phase 5 — the training report: one self-contained HTML page (inline SVG, no scripts).

render(ctx) takes plain data from scripts/train_winprob.py so it can be tested without
xgboost or scikit-learn installed.
"""

from __future__ import annotations

import html
import math

import pandas as pd

COLORS = {"simple": "#888888", "xgb": "#1f77b4", "xgb_iso": "#2ca02c", "mlp": "#d62728"}
FALLBACK = ["#9467bd", "#8c564b", "#e377c2", "#17becf"]


def _color(key: str, i: int) -> str:
    return COLORS.get(key, FALLBACK[i % len(FALLBACK)])


def _esc(s) -> str:
    return html.escape(str(s))


def _pct(v, digits=1) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100 * v:.{digits}f}"


def _bytes(n) -> str:
    if n is None:
        return "—"
    return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB" if n >= 1e3 else f"{n} B"


# ---- SVG charts -----------------------------------------------------------

def svg_reliability(series: list[tuple[str, str, pd.DataFrame]], size: int = 360, title: str = "") -> str:
    """series: (key, label, reliability table). Diagonal = perfect calibration; shaded
    band = the ±4-pt bin tolerance."""
    pad = 40
    w = h = size
    sx = lambda v: pad + v * (w - 2 * pad)
    sy = lambda v: h - pad - v * (h - 2 * pad)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" class="chart">']
    if title:
        parts.append(f'<text x="{w/2}" y="14" text-anchor="middle" class="t">{_esc(title)}</text>')
    band = [(0, 0.04), (1, 1.04), (1, 0.96), (0, -0.04)]
    parts.append('<polygon fill="#eee" points="' + " ".join(f"{sx(x)},{sy(min(max(y,0),1))}" for x, y in band) + '"/>')
    for v in (0, 0.25, 0.5, 0.75, 1):
        parts.append(f'<line x1="{sx(v)}" y1="{sy(0)}" x2="{sx(v)}" y2="{sy(0)+4}" stroke="#999"/>'
                     f'<text x="{sx(v)}" y="{sy(0)+16}" text-anchor="middle" class="a">{v:.0%}</text>'
                     f'<text x="{sx(0)-6}" y="{sy(v)+4}" text-anchor="end" class="a">{v:.0%}</text>')
    parts.append(f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" stroke="#bbb" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{w/2}" y="{h-4}" text-anchor="middle" class="a">predicted P(Blue wins)</text>')
    parts.append(f'<text x="10" y="{h/2}" transform="rotate(-90 10 {h/2})" text-anchor="middle" class="a">Blue actually won</text>')
    for i, (key, label, rel) in enumerate(series):
        c = _color(key, i)
        pts = " ".join(f"{sx(r.mean_pred)},{sy(r.observed)}" for r in rel.itertuples())
        parts.append(f'<polyline fill="none" stroke="{c}" stroke-width="2" points="{pts}"/>')
        for r in rel.itertuples():
            parts.append(f'<circle cx="{sx(r.mean_pred)}" cy="{sy(r.observed)}" r="3" fill="{c}">'
                         f'<title>{_esc(label)}: predicted {r.mean_pred:.1%}, observed {r.observed:.1%}, '
                         f'{r.games} games / {r.rows} rows</title></circle>')
        parts.append(f'<rect x="{pad+6}" y="{pad-8+i*16}" width="10" height="10" fill="{c}"/>'
                     f'<text x="{pad+20}" y="{pad+1+i*16}" class="a">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_lines(df: pd.DataFrame, x: str, ys: list[tuple[str, str, str]], ylabel: str,
              w: int = 560, h: int = 260, ylim: tuple[float, float] | None = None, fmt=lambda v: f"{v:.2f}") -> str:
    """ys: (column, key for colour, label)."""
    pad_l, pad_b, pad_t, pad_r = 48, 30, 12, 12
    xs = df[x].to_numpy(float)
    vals = pd.concat([df[c] for c, _, _ in ys]).to_numpy(float)
    lo, hi = ylim or (float(vals.min()), float(vals.max()))
    if hi <= lo:
        hi = lo + 1
    x0, x1 = xs.min(), xs.max() if xs.max() > xs.min() else xs.min() + 1
    sx = lambda v: pad_l + (v - x0) / (x1 - x0) * (w - pad_l - pad_r)
    sy = lambda v: h - pad_b - (v - lo) / (hi - lo) * (h - pad_b - pad_t)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" class="chart">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        parts.append(f'<line x1="{pad_l}" y1="{sy(v)}" x2="{w-pad_r}" y2="{sy(v)}" stroke="#eee"/>'
                     f'<text x="{pad_l-4}" y="{sy(v)+4}" text-anchor="end" class="a">{fmt(v)}</text>')
    step = 5 if x1 - x0 > 12 else 1
    for v in range(int(math.ceil(x0 / step) * step), int(x1) + 1, step):
        parts.append(f'<text x="{sx(v)}" y="{h-pad_b+14}" text-anchor="middle" class="a">{v}</text>')
    parts.append(f'<text x="{w/2}" y="{h-2}" text-anchor="middle" class="a">{_esc(x)}</text>'
                 f'<text x="10" y="{h/2}" transform="rotate(-90 10 {h/2})" text-anchor="middle" class="a">{_esc(ylabel)}</text>')
    for i, (col, key, label) in enumerate(ys):
        c = _color(key, i)
        pts = " ".join(f"{sx(a)},{sy(b)}" for a, b in zip(xs, df[col].to_numpy(float)))
        parts.append(f'<polyline fill="none" stroke="{c}" stroke-width="2" points="{pts}"/>')
        parts.append(f'<rect x="{pad_l+8+i*130}" y="{pad_t}" width="10" height="10" fill="{c}"/>'
                     f'<text x="{pad_l+22+i*130}" y="{pad_t+9}" class="a">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_bars(items: list[tuple[str, float]], w: int = 560, bar_h: int = 16, fmt=lambda v: f"{v:.1%}") -> str:
    lab = 150
    h = len(items) * (bar_h + 4) + 8
    top = max((v for _, v in items), default=1) or 1
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" class="chart">']
    for i, (name, v) in enumerate(items):
        y = 4 + i * (bar_h + 4)
        bw = (w - lab - 60) * v / top
        parts.append(f'<text x="{lab-6}" y="{y+bar_h-4}" text-anchor="end" class="a">{_esc(name)}</text>'
                     f'<rect x="{lab}" y="{y}" width="{bw:.1f}" height="{bar_h}" fill="{COLORS["xgb"]}"/>'
                     f'<text x="{lab+bw+4}" y="{y+bar_h-4}" class="a">{fmt(v)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---- tables ---------------------------------------------------------------

class metrics_slices:
    """Minimal stand-in exposing `.slices`, so extra slice tables share the code path."""

    def __init__(self, slices: pd.DataFrame):
        self.slices = slices


def table(df: pd.DataFrame, formats: dict | None = None, row_class=None) -> str:
    formats = formats or {}
    head = "".join(f"<th>{_esc(c)}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cls = f' class="{row_class(r)}"' if row_class else ""
        cells = "".join(f"<td>{_esc(formats[c](r[c]) if c in formats else r[c])}</td>" for c in df.columns)
        body.append(f"<tr{cls}>{cells}</tr>")
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


CSS = """
body{font:14px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}
h1{font-size:22px;margin-bottom:4px}h2{font-size:17px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}
.sub{color:#666}.grid{display:flex;flex-wrap:wrap;gap:16px}
table{border-collapse:collapse;margin:8px 0;font-size:13px}th,td{padding:4px 10px;border-bottom:1px solid #eee;text-align:right}
th{background:#f6f6f6}td:first-child,th:first-child{text-align:left}
tr.fail td{background:#fdecea}tr.pass td{}
.verdict{display:inline-block;padding:6px 12px;border-radius:6px;font-weight:600;margin:6px 0}
.ok{background:#e6f4ea;color:#1e6b34}.bad{background:#fdecea;color:#a12622}
.chart text.a{font-size:11px;fill:#555}.chart text.t{font-size:13px;fill:#222;font-weight:600}
.note{color:#555;font-size:13px;max-width:820px}
"""


def render(ctx: dict) -> str:
    """ctx keys: dataset (dict), models (list of dicts: key, label, summary, n_params,
    size_bytes, train_s, ms_per_game, gate_passed), benchmark_gain (dict key -> (mean, lo, hi)),
    reliability (dict key -> DataFrame), gate (dict: name, result (GateResult), calibrated (bool)),
    gates (dict key -> GateResult) for slice tables, by_minute (DataFrame), importance (dict),
    mlp_curve (list), wpa (dict), production (str key)."""
    ds = ctx["dataset"]
    models = ctx["models"]
    out = [f"<!doctype html><meta charset='utf-8'><title>Win-probability model — Phase 5</title><style>{CSS}</style>",
           "<h1>Win-probability model — Phase 5</h1>",
           f"<p class='sub'>{ds['games']:,} games ({ds['rows']:,} game-minutes) · tiers {_esc(', '.join(ds['tiers']))} · "
           f"patches {_esc(', '.join(ds['patches']))} · split by game: train {ds['split_games']['train']:,} / "
           f"val {ds['split_games']['val']:,} / test {ds['split_games']['test']:,} · built {_esc(ds['built'])}</p>"]

    # verdict
    g = ctx["gate"]
    res = g["result"]
    n_fail = int((~res.checks["passed"]).sum())
    verdict = ("PASSES" if res.passed else f"FAILS ({n_fail} of {len(res.checks)} checks)")
    how = f" ({g['how']})" if g.get("how") else (
        " after isotonic correction (fitted on validation games)" if g["calibrated"] else "")
    bench = ctx["benchmark_gain"].get(ctx["production"])
    out.append(f"<div class='verdict {'ok' if res.passed else 'bad'}'>Calibration gate: {_esc(g['name'])} {verdict}{how}</div>")
    if bench:
        beat = bench[1] > 0
        out.append(f"<div class='verdict {'ok' if beat else 'bad'}'>Benchmark: log loss {bench[0]:.4f} lower than "
                   f"gold-diff logistic (90% range {bench[1]:.4f} to {bench[2]:.4f}) — "
                   f"{'beaten' if beat else 'NOT clearly beaten'}</div>")

    # comparison table
    out.append("<h2>Side by side (test games)</h2>")
    rows = []
    for m in models:
        s = m["summary"]
        gain = ctx["benchmark_gain"].get(m["key"])
        rows.append({
            "model": m["label"], "log loss": f"{s['log_loss']:.4f}", "Brier": f"{s['brier']:.4f}",
            "accuracy": _pct(s["accuracy"]), "ECE (pts)": _pct(s["ece"], 2),
            "vs benchmark": "—" if gain is None else f"{gain[0]:+.4f} [{gain[1]:+.4f}, {gain[2]:+.4f}]",
            "gate": "pass" if m.get("gate_passed") else "fail",
            "learned numbers": f"{m['n_params']:,}" if m.get("n_params") is not None else "—",
            "size": _bytes(m.get("size_bytes")),
            "train time": f"{m['train_s']:.1f}s" if m.get("train_s") is not None else "—",
            "ms / game": f"{m['ms_per_game']:.2f}" if m.get("ms_per_game") is not None else "—",
        })
    out.append(table(pd.DataFrame(rows)))
    out.append("<p class='note'>Lower log loss and Brier are better. Accuracy counts how often the favourite won, "
               "which is a weak measure: at 2:00 nobody can call the game. <b>vs benchmark</b> is how much lower each model's log loss is "
               "than the gold-diff logistic, with a 90% range from resampling games. <b>Gate</b> is the agreed calibration check run on each "
               "model. Only the production model has to pass it, and only the production model gets the out-of-fold tier and patch "
               "checks. The comparison models' gate column covers overall and phase on test games.</p>")

    # volatility: decisive and reactive, not hedged toward 50%
    vol = ctx.get("volatility") or {}
    if vol:
        out.append("<h2>Does the curve stay decisive and swing when it should? (test games)</h2>")
        rows = []
        for m in models:
            v = vol.get(m["key"])
            if v is None:
                continue
            for r in v.itertuples():
                rows.append({"model": m["label"], "phase": r.phase, "above 90 / below 10": _pct(r.extreme, 0),
                             "swing q50": _pct(r.swing_q50), "swing q90": _pct(r.swing_q90),
                             "swing q99": _pct(r.swing_q99), "quiet minute": _pct(r.quiet_move),
                             "decisive minute": f"{100 * r.event_move:+.1f}", "decisive minutes": f"{r.events:,}"})
        out.append(table(pd.DataFrame(rows).sort_values(["phase", "model"], kind="stable")))
        out.append("<p class='note'>All in percentage points. <b>Above 90 / below 10</b>: how often the model is very sure. "
                   "<b>Swing</b>: how far the curve moves in one minute. <b>Quiet minute</b>: the average move when nothing decisive happened. "
                   "<b>Decisive minute</b>: the average move toward the team that just took Baron or Elder, took an inhibitor, or won a fight by "
                   "3+ kills. Late game should be both sure when the state is lopsided and very responsive to those events. "
                   "Pulling everything toward 50% would pass calibration while failing this table.</p>")

    tuning = ctx.get("tuning")
    if tuning is not None and len(tuning):
        out.append("<h2>XGBoost settings search (validation games only)</h2>")
        t = tuning.copy()
        t = pd.DataFrame({
            "depth": t["max_depth"], "min leaf weight": t["min_child_weight"], "trees": t["trees"],
            "val log loss": t["val_log_loss"].map(lambda v: f"{v:.4f}"),
            "late log loss": t["val_late_log_loss"].map(lambda v: f"{v:.4f}"),
            "late ECE": t["val_late_ece"].map(lambda v: _pct(v, 2)),
            "late >90/<10": t["late_extreme"].map(lambda v: _pct(v, 0)),
            "late swing q90": t["late_swing_q90"].map(_pct),
            "late decisive move": t["late_event_move"].map(lambda v: f"{100 * v:+.1f}"),
            "train": t["train_s"].map(lambda v: f"{v:.0f}s"),
            "chosen": t["chosen"].map({True: "◀ chosen", False: ""}),
        })
        out.append(table(t, row_class=lambda r: "pass"))
        out.append("<p class='note'>Chosen by lowest validation log loss, without looking at test games. "
                   "v1 was depth 4 with min leaf weight 50. Min leaf weight sums p(1−p) over a leaf's rows, so it limits lopsided "
                   "(decisive) states more than even ones. That's why the late columns are shown for every candidate.</p>")

    # reliability
    out.append("<h2>Calibration (reliability diagrams, test games)</h2><div class='grid'>")
    series = [(m["key"], m["label"], ctx["reliability"][m["key"]]) for m in models if m["key"] in ctx["reliability"]]
    out.append(svg_reliability(series, title="all minutes"))
    prod_bins = res.bins
    for name in ("early (<14)", "mid (14–25)", "late (25+)"):
        if ("phase", name) in prod_bins:
            out.append(svg_reliability([(ctx["production"], g["name"], prod_bins[("phase", name)])],
                                       size=240, title=name))
    out.append("</div><p class='note'>Points on the dashed diagonal = calibrated. Grey band = ±4 pts. Hover a point for its counts.</p>")

    # gate panel
    out.append(f"<h2>Gate checks — {_esc(g['name'])}</h2>")
    chk = res.checks.copy()
    chk["value"] = chk.apply(lambda r: f"{100*r['value']:+.2f}" if r["check"] != "ECE" else f"{100*r['value']:.2f}", axis=1)
    chk["limit"] = chk.apply(lambda r: f"≤ {100*r['limit']:.0f}" if r["check"] == "ECE" else f"± {100*r['limit']:.0f}", axis=1)
    chk["noise (90%)"] = [f"{100*a:+.1f} … {100*b:+.1f}" for a, b in zip(res.checks["noise_lo"], res.checks["noise_hi"])]
    chk["result"] = res.checks["passed"].map({True: "pass", False: "FAIL"})
    chk["scored on"] = res.checks["source"] if "source" in res.checks else "test games"
    chk = chk[["scored on", "group", "slice", "check", "value", "limit", "noise (90%)", "result"]]
    out.append(table(chk, row_class=lambda r: "fail" if r["result"] == "FAIL" else "pass"))
    out.append("<p class='note'>Values are in percentage points. The noise column is a 90% range from resampling whole games. "
               "If a failing check's range comfortably includes values inside the limit, the failure may be sampling noise in a small slice. "
               "It still counts as a failure; the range is there to help read it.</p>")

    # slices across models
    out.append("<h2>By phase, tier and patch</h2>")
    rows = []
    sources = [(m["label"], ctx["gates"].get(m["key"])) for m in models]
    oof = res.slices[res.slices["source"] != "test games"] if "source" in res.slices else res.slices.iloc[:0]
    if len(oof):
        sources.append((f"{g['name']} (out-of-fold)", metrics_slices(oof)))
    for label, gr in sources:
        if gr is None:
            continue
        for r in gr.slices.itertuples():
            rows.append({"model": label, "scored on": getattr(r, "source", "test games"),
                         "slice": f"{r.group}: {r.slice}", "games": f"{r.games:,}",
                         "rows": f"{r.rows:,}", "Blue won": _pct(r.blue_win_rate), "log loss": f"{r.log_loss:.4f}", "accuracy": _pct(r.accuracy),
                         "ECE (pts)": _pct(r.ece, 2)})
    if rows:
        t = pd.DataFrame(rows).sort_values(["slice", "model"], kind="stable")
        out.append(table(t))
        out.append("<p class='note'><b>Blue won</b> is the slice's real Blue win rate. In a small slice it can drift far from 50% "
                   "by chance (with 265 games, ±3 pts is one standard error). A model can't predict that drift, so it shows up "
                   "as a calibration gap in that slice.</p>")

    # by minute
    bm = ctx["by_minute"]
    if bm is not None and len(bm):
        out.append("<h2>By game minute (test games)</h2><div class='grid'>")
        out.append(svg_lines(bm, "minute", [(f"{m['key']}_acc", m["key"], m["label"]) for m in models],
                             "accuracy", ylim=(0.5, 1.0), fmt=lambda v: f"{v:.0%}"))
        out.append(svg_lines(bm, "minute", [(f"{m['key']}_ll", m["key"], m["label"]) for m in models],
                             "log loss"))
        out.append("</div>")

    # importance
    if ctx.get("importance"):
        imp = sorted(ctx["importance"].items(), key=lambda kv: -kv[1])
        out.append("<h2>What XGBoost leans on (share of total split gain)</h2>")
        out.append(svg_bars(imp))
        out.append("<p class='note'>Gain is how much each feature's splits reduced the loss. Correlated features share credit, "
                   "so a low bar does not mean the feature is useless (gold and XP diff overlap heavily).</p>")

    # mlp curve
    if ctx.get("mlp_curve"):
        c = pd.DataFrame(ctx["mlp_curve"], columns=["epoch", "train", "val"])
        out.append("<h2>Neural net training curve</h2>")
        out.append(svg_lines(c, "epoch", [("train", "simple", "train (50k-row sample)"), ("val", "mlp", "validation")],
                             "log loss", fmt=lambda v: f"{v:.3f}"))

    # swing distribution
    wpa = ctx.get("wpa")
    if wpa:
        out.append("<h2>How big is a normal one-minute swing? (held-out games)</h2>")
        rows = [{"minute": int(k), **{q: _pct(v[q]) for q in ("q50", "q75", "q90", "q95", "q99")}, "rows": f"{v['rows']:,}"}
                for k, v in wpa["by_minute"].items() if int(k) % 5 == 0 or int(k) in (1, 2, 3)]
        out.append(table(pd.DataFrame(rows)))
        out.append("<p class='note'>|ΔP(Blue)| between consecutive minutes, in percentage points. q90 = 90% of minutes swing less than this. "
                   "Phase 6 measures moments against this table: a swing is 'unusual' only well into the tail.</p>")
    return "\n".join(out)
