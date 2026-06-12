"""Chart drawing + a plain-text summary for the single-ticker dashboard view.

Drawing helpers take a matplotlib Axes (created and owned by the dashboard), so
this module has no Tk dependency. Used by dashboard.py.
"""
from __future__ import annotations

import pandas as pd

from core.scoring import is_scored

NAVY = "#0B2545"
TEAL = "#1B9AAA"
ORANGE = "#E08E0B"
GREEN = "#2e9e3f"
RED = "#c0392b"
GREY = "#9aa0a6"
PURPLE = "#7b4fb3"
BAND = "#8a94a6"

# Overlay windows. EMA span and the Bollinger window/σ are standard defaults.
SMA_WINDOWS = (20, 50, 200)
EMA_SPAN = 20
BB_WINDOW = 20
BB_STD = 2.0
_SMA_COLORS = {20: TEAL, 50: ORANGE, 200: PURPLE}


def _sma(closes: list[float], window: int) -> pd.Series:
    return pd.Series(closes).rolling(window, min_periods=max(2, window // 2)).mean()


def _ema(closes: list[float], span: int) -> pd.Series:
    return pd.Series(closes).ewm(span=span, adjust=False, min_periods=span).mean()


def _bollinger(closes: list[float], window: int, n_std: float):
    s = pd.Series(closes)
    mid = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    return mid, mid + n_std * sd, mid - n_std * sd


def draw_price(ax, bars: list[dict], ticker: str, period: str) -> float | None:
    """Price line with SMA 20/50/200, EMA 20 and Bollinger Band overlays.

    Every overlay is computed from the closing prices in `bars` (no extra data
    fetch). Returns the % change over the window.
    """
    ax.clear()
    closes = [b["c"] for b in bars] if bars else []
    if len(closes) < 2:
        ax.text(0.5, 0.5, "no price data", ha="center", va="center",
                transform=ax.transAxes, color=GREY)
        ax.set_xticks([]); ax.set_yticks([])
        return None

    if all(b.get("t") for b in bars):
        xs = pd.to_datetime([b["t"] for b in bars], unit="ms")
    else:
        xs = list(range(len(closes)))

    # Bollinger Bands drawn first so they sit behind the price line.
    if len(closes) >= BB_WINDOW:
        _, upper, lower = _bollinger(closes, BB_WINDOW, BB_STD)
        ax.plot(xs, upper, color=BAND, lw=0.8, ls=":", alpha=0.8)
        ax.plot(xs, lower, color=BAND, lw=0.8, ls=":", alpha=0.8,
                label=f"Bollinger {BB_WINDOW}·{BB_STD:g}σ")
        ax.fill_between(xs, lower, upper, color=BAND, alpha=0.08)

    ax.plot(xs, closes, color=NAVY, lw=1.6, label="Close")

    for w in SMA_WINDOWS:
        if len(closes) >= max(2, w // 2):
            ax.plot(xs, _sma(closes, w), color=_SMA_COLORS[w], lw=1.1, label=f"SMA {w}")
    if len(closes) >= EMA_SPAN:
        ax.plot(xs, _ema(closes, EMA_SPAN), color=GREEN, lw=1.1, ls="--",
                label=f"EMA {EMA_SPAN}")

    chg = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0.0
    ax.set_title(f"{ticker} price · {period}   ({chg:+.1f}%)",
                 fontsize=11, fontweight="bold", color=NAVY, loc="left")
    ax.legend(loc="upper left", fontsize=7, frameon=False, ncol=2)
    ax.grid(True, alpha=0.25, lw=0.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(labelsize=8)
    return chg


def draw_signal_bars(ax, items: list[tuple[str, float]], composite: float | None) -> None:
    """Horizontal bar chart of each signal's 1-10 score, colored by value."""
    ax.clear()
    if not items:
        ax.text(0.5, 0.5, "no scored signals", ha="center", va="center",
                transform=ax.transAxes, color=GREY)
        ax.set_xticks([]); ax.set_yticks([])
        return
    names = [n for n, _ in items]
    tens = [t for _, t in items]
    colors = [GREEN if t >= 6.5 else (RED if t < 4.5 else GREY) for t in tens]
    y = list(range(len(items)))
    ax.barh(y, tens, color=colors, height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0, 10.6)
    for i, t in enumerate(tens):
        ax.text(t + 0.15, i, f"{t:.1f}", va="center", fontsize=7, color="#333")
    if is_scored(composite):
        ax.axvline(composite, color=NAVY, ls="--", lw=1)
        ax.text(composite, -0.7, f"composite {composite:.1f}", fontsize=7,
                color=NAVY, ha="center")
    ax.set_title("Signal scores (1–10)", fontsize=11, fontweight="bold", color=NAVY, loc="left")
    ax.grid(True, axis="x", alpha=0.25, lw=0.5)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(labelsize=8)


def summarize(report: dict, info: dict | None, chg: float | None) -> str:
    """Plain-English roll-up of the analysis."""
    t = report["ticker"]
    comp = report["composite"]
    name = (info or {}).get("name", t)
    scored = [(n, s["ten"]) for n, s in report["signals"].items() if is_scored(s["ten"])]
    scored.sort(key=lambda x: x[1], reverse=True)

    lines = []
    if is_scored(comp):
        lines.append(f"{name} ({t}) — Composite {comp:.1f}/10 ({report['composite_label']}), "
                     f"average of {report['n_scored']} signals.")
    else:
        lines.append(f"{name} ({t}) — no composite yet.")
    if chg is not None:
        direction = "up" if chg >= 0 else "down"
        lines.append(f"Price is {direction} {abs(chg):.1f}% over the window.")
    if scored:
        top = ", ".join(f"{n} {v:.1f}" for n, v in scored[:3])
        lines.append(f"Strongest: {top}.")
        if len(scored) > 3:
            bot = ", ".join(f"{n} {v:.1f}" for n, v in scored[-2:])
            lines.append(f"Weakest: {bot}.")
    ins = report["signals"].get("insider", {}).get("native_rating")
    if ins and ins not in ("—", "N/A") and not str(ins).startswith("ERR"):
        lines.append(f"Insider activity: {ins}.")
    return "\n".join(lines)
