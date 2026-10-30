"""v0 percentile report: how one player's game compares with players at the same rank,
role and minute. Descriptive only — no model, no verdicts (CLAUDE.md: comparative claims,
never "objectively wrong").

Pure function from (frames rows, baselines) to text, so it's testable without files.
"""

from __future__ import annotations

from .lookup import Baselines, Placement
from .stats import ROLE_NAMES, Stat, stats_for

STAND_OUT = 15.0  # top/bottom 15% gets listed under "what stands out"


def fmt_value(stat: Stat, v) -> str:
    if v is None:
        return "—"
    if stat.name == "kill_participation":
        return f"{100 * v:.0f}%"
    if stat.name == "cs_per_min":
        return f"{v:.1f}"
    if stat.name == "cc_time":
        return f"{v / 1000:,.0f}s"
    if stat.name.endswith("_diff"):
        return f"{v:+,.0f}"
    return f"{v:,.0f}"


def share_better_worse(stat: Stat, p: Placement) -> tuple[float, float]:
    """(% of players doing as well or better, % doing as badly or worse), ties included —
    so '0 deaths' with a third of players also on 0 reads as 'top 33%', not 'top 0%'."""
    if stat.better == "lower":
        return p.pct_lower + p.pct_tied, p.pct_higher + p.pct_tied
    return p.pct_higher + p.pct_tied, p.pct_lower + p.pct_tied


def cell(stat: Stat, p: Placement | None, value) -> str:
    if value is None:
        return "—"
    if p is None:
        return f"{fmt_value(stat, value)} (no data)"
    if stat.better == "neither":
        return f"{fmt_value(stat, value)} · p{p.percentile:.0f}"
    better = p.pct_lower if stat.better == "higher" else p.pct_higher
    tie = f" ={p.pct_tied:.0f}%" if stat.count and p.pct_tied >= 5 else ""
    return f"{fmt_value(stat, value)} · beats {better:.0f}%{tie}"


def checkpoints(last_minute: int) -> list[int]:
    picks = [m for m in (10, 15, 20) if m < last_minute]
    return picks + [last_minute]


def render(
    rows: list[dict],
    role: str,
    primary: Baselines,
    compare: Baselines | None = None,
    header: list[str] | None = None,
) -> str:
    """rows: one player's frames rows (any order)."""
    by_min = {r["minute"]: r for r in rows}
    last = max(by_min)
    cps = checkpoints(last)
    tier = (primary.tier or "?").capitalize()
    who = ROLE_NAMES.get(role, role)
    stats = stats_for(role)
    width = 22
    out = list(header or [])

    def label_for(m):
        if m != last:
            return f"{m}:00"
        s = round(by_min[m].get("t_ms", m * 60_000) / 1000)  # the final snapshot is at game end
        return f"end {s // 60}:{s % 60:02d}"

    # 1 — placement table
    out += ["", f"How you compare with {tier} {who} at the same minute",
            f"  'beats X%' = share of {tier} {who} you did better than; '=Y%' = share tied with you;",
            "  'pX' = percentile, for stats where higher isn't better or worse (depends on champion)", ""]
    out.append(f"  {'':26}" + "".join(f"{label_for(m):>{width}}" for m in cps))
    group = None
    for s in stats:
        if s.group != group:
            group = s.group
            out.append(f"  {group}")
        cells = [cell(s, primary.place(role, m, s.name, by_min[m].get(s.name)), by_min[m].get(s.name)) for m in cps]
        out.append(f"    {s.label:24}" + "".join(f"{c:>{width}}" for c in cells))

    # 2 — typical values
    comp = (compare.tier or "").capitalize() if compare else None
    out += ["", f"Typical values ({tier}" + (f" / {comp}" if comp else "") + " median) for comparison",
            "  (lane differences are left out: their typical value is 0 by definition)", ""]
    out.append(f"  {'':26}" + "".join(f"{label_for(m):>{width}}" for m in cps))
    for s in stats:
        if s.group == "Lane matchup":
            continue
        vals = []
        for m in cps:
            a = fmt_value(s, primary.median(role, m, s.name))
            b = fmt_value(s, compare.median(role, m, s.name)) if compare else None
            vals.append(a + (f" / {b}" if b is not None else ""))
        out.append(f"    {s.label:24}" + "".join(f"{v:>{width}}" for v in vals))

    # 3 — what stands out: most extreme checkpoint per stat
    notes = []
    for s in stats:
        if s.better == "neither":
            continue
        best = None
        for m in cps:
            p = primary.place(role, m, s.name, by_min[m].get(s.name))
            if p is None:
                continue
            top, bottom = share_better_worse(s, p)
            for kind, share in (("top", top), ("bottom", bottom)):
                if share <= STAND_OUT and (best is None or share < best[0]):
                    best = (share, kind, m, p)
        if best:
            share, kind, m, p = best
            cmp_med = compare.median(role, m, s.name) if compare else None
            typical = "" if s.group == "Lane matchup" else "  (typical " + fmt_value(s, p.median) + (
                f", {comp} {fmt_value(s, cmp_med)}" if cmp_med is not None else "") + ")"
            notes.append((share, f"  {kind:6} {share:4.0f}%  {s.label} at {label_for(m)}: "
                                 f"{fmt_value(s, p.value)}{typical}"))
    out += ["", f"What stands out (top or bottom {STAND_OUT:.0f}% of {tier} {who}, ties included)", ""]
    out += [line for _, line in sorted(notes)] or ["  nothing in the top or bottom 15% — a fairly typical game on paper"]
    return "\n".join(out)
