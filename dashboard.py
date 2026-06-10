"""FlexTrade equity research dashboard — native desktop app (Tkinter).

Run:
    python dashboard.py

Two tabs:
  * Single Ticker — type a ticker, see each person's signal under its category
    panel plus a composite readout; analysed tickers stack in a table.
  * Recommender   — enter a list of tickers or a named universe, rank them by
    composite (best to long at the top, best to short at the bottom), and
    export the ranking to CSV.

PLACEHOLDER: every signal currently returns an arbitrary score (see modules/*.py).
The UI reads from the same core functions the CLI uses, so when owners drop in
real logic nothing here needs to change.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from core.recommender import rank
from core.registry import signal_specs
from core.runner import analyze_ticker, export_csv
from core.universe import available, resolve

PERIODS = ["6mo", "1y", "2y", "5y"]


def _fmt(score) -> str:
    if isinstance(score, (int, float)) and not (isinstance(score, float) and math.isnan(score)):
        return f"{score:+.3f}"
    return "—"


class Dashboard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FlexTrade Equity Research")
        self.geometry("1100x780")

        self.specs = signal_specs()
        self.signal_names = [s["name"] for s in self.specs]
        self.rows: dict[str, dict] = {}        # single-ticker table: ticker -> scores
        self.category_trees: dict[str, ttk.Treeview] = {}
        self._last_ranking: pd.DataFrame | None = None

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        single = ttk.Frame(nb)
        recommender = ttk.Frame(nb)
        nb.add(single, text="Single Ticker")
        nb.add(recommender, text="Recommender")

        self._build_single(single)
        self._build_recommender(recommender)

    # ================= Single Ticker tab =================
    def _build_single(self, parent: ttk.Frame) -> None:
        frm = ttk.Frame(parent, padding=12)
        frm.pack(fill="x")

        ttk.Label(frm, text="Enter Ticker:", font=("", 14)).pack(side="left")
        self.ticker_var = tk.StringVar()
        entry = ttk.Entry(frm, textvariable=self.ticker_var, width=14, font=("", 14))
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda _e: self.on_analyze())
        entry.focus()

        ttk.Label(frm, text="Period:").pack(side="left", padx=(12, 4))
        self.period_var = tk.StringVar(value="2y")
        ttk.Combobox(frm, textvariable=self.period_var, values=PERIODS,
                     width=6, state="readonly").pack(side="left")
        ttk.Button(frm, text="Analyze", command=self.on_analyze).pack(side="left", padx=12)

        self.composite_var = tk.StringVar(value="Composite: —")
        ttk.Label(frm, textvariable=self.composite_var, font=("", 14, "bold")).pack(side="right")

        # category panels (one per distinct SIGNAL_CATEGORY, first-seen order)
        container = ttk.Frame(parent, padding=(12, 0))
        container.pack(fill="x")
        categories: list[str] = []
        for s in self.specs:
            if s["category"] not in categories:
                categories.append(s["category"])
        for cat in categories:
            panel = ttk.LabelFrame(container, text=cat, padding=6)
            panel.pack(side="left", fill="both", expand=True, padx=5, pady=8)
            tree = ttk.Treeview(panel, columns=("owner", "score", "rating"),
                                show="tree headings", height=5)
            tree.heading("#0", text="Signal")
            tree.heading("owner", text="Owner")
            tree.heading("score", text="Their Score")
            tree.heading("rating", text="Their Rating")
            tree.column("#0", width=150)
            tree.column("owner", width=64, anchor="center")
            tree.column("score", width=80, anchor="center")
            tree.column("rating", width=120, anchor="center")
            tree.pack(fill="both", expand=True)
            tree.bind("<<TreeviewSelect>>", self._on_signal_select)
            for s in self.specs:
                if s["category"] == cat:
                    tree.insert("", "end", iid=s["name"], text=s["name"],
                                values=(s["owner"], "—", "—"))
            self.category_trees[cat] = tree

        # breakdown pane — how each rating was computed (their own logic)
        bd = ttk.LabelFrame(parent, text="How each rating was computed (click a signal to focus)", padding=6)
        bd.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.breakdown = tk.Text(bd, height=10, wrap="word", state="disabled",
                                 font=("Menlo", 11))
        bdbar = ttk.Scrollbar(bd, orient="vertical", command=self.breakdown.yview)
        self.breakdown.configure(yscrollcommand=bdbar.set)
        bdbar.pack(side="right", fill="y")
        self.breakdown.pack(side="left", fill="both", expand=True)

        # accumulating table (cross-ticker comparison uses the normalized -1..+1
        # scale so columns are comparable and feed the composite)
        tbl = ttk.LabelFrame(parent, text="Tickers — normalized -1..+1 for comparison", padding=6)
        tbl.pack(fill="both", expand=True, padx=12, pady=8)
        cols = ["Ticker", *self.signal_names, "Composite"]
        self.table = ttk.Treeview(tbl, columns=cols, show="headings", height=6)
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=120, anchor="center")
        self.table.column("Ticker", width=80, anchor="w")
        xbar = ttk.Scrollbar(tbl, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=xbar.set)
        self.table.pack(fill="both", expand=True)
        xbar.pack(fill="x")

        foot = ttk.Frame(parent, padding=12)
        foot.pack(fill="x")
        ttk.Button(foot, text="Export CSV", command=self.on_export).pack(side="left")
        ttk.Button(foot, text="Clear Table", command=self.on_clear).pack(side="left", padx=8)
        self.status_var = tk.StringVar(
            value="Placeholder data — every signal returns an arbitrary score until owners add real logic."
        )
        ttk.Label(foot, textvariable=self.status_var, foreground="#666").pack(side="right")

    def on_analyze(self) -> None:
        ticker = self.ticker_var.get().strip().upper()
        if not ticker:
            return
        report = analyze_ticker(ticker, period=self.period_var.get())
        self.last_report = report

        for cat, tree in self.category_trees.items():
            for s in self.specs:
                if s["category"] != cat:
                    continue
                sig = report["signals"].get(s["name"], {})
                tree.item(s["name"], values=(s["owner"], sig.get("native_score", "—"),
                                             sig.get("native_rating", "—")))

        self.composite_var.set(
            f"Composite: {_fmt(report['composite'])}  ({report['composite_rating']})"
        )
        self._render_breakdown()  # all signals

        store: dict = {}
        values = [ticker]
        for name in self.signal_names:
            score = report["signals"].get(name, {}).get("score")
            store[name] = score
            values.append(_fmt(score))
        store["composite"] = report["composite"]
        values.append(_fmt(report["composite"]))

        if ticker in self.rows:
            self.table.item(ticker, values=values)
        else:
            self.table.insert("", "end", iid=ticker, values=values)
        self.rows[ticker] = store
        self.status_var.set(f"Analyzed {ticker} ({len(self.rows)} in table).")

    def _on_signal_select(self, event) -> None:
        sel = event.widget.selection()
        if sel:
            self._render_breakdown(only=sel[0])

    def _render_breakdown(self, only: str | None = None) -> None:
        """Fill the breakdown pane with each signal's native rating + reasoning.
        If `only` is a signal name, show just that one."""
        report = getattr(self, "last_report", None)
        self.breakdown.configure(state="normal")
        self.breakdown.delete("1.0", "end")
        if report:
            self.breakdown.insert("end", f"{report['ticker']}\n")
            for name, sig in report["signals"].items():
                if only and name != only:
                    continue
                self.breakdown.insert("end", f"\n[{sig['owner']} · {name}] → {sig['native_rating']}\n")
                for line in sig.get("breakdown", []):
                    self.breakdown.insert("end", f"   • {line}\n")
        self.breakdown.configure(state="disabled")

    def on_export(self) -> None:
        if not self.rows:
            messagebox.showinfo("Export CSV", "Analyze at least one ticker first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="ratings.csv",
            initialdir="data", filetypes=[("CSV", "*.csv")],
        )
        if path:
            df = pd.DataFrame.from_dict(self.rows, orient="index")
            df.index.name = "ticker"
            export_csv(df, path)
            self.status_var.set(f"Wrote {path}")

    def on_clear(self) -> None:
        for iid in self.table.get_children():
            self.table.delete(iid)
        self.rows.clear()
        self.status_var.set("Table cleared.")

    # ================= Recommender tab =================
    def _build_recommender(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text="Tickers or Universe:", font=("", 13)).pack(side="left")
        self.rec_input = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.rec_input, width=40, font=("", 12))
        entry.pack(side="left", padx=8)
        entry.bind("<Return>", lambda _e: self.on_rank())

        ttk.Label(top, text="Universe:").pack(side="left", padx=(12, 4))
        self.rec_universe = tk.StringVar()
        combo = ttk.Combobox(top, textvariable=self.rec_universe, values=available(),
                             width=14, state="readonly")
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self.rec_input.set(self.rec_universe.get()))

        ttk.Label(top, text="Period:").pack(side="left", padx=(12, 4))
        self.rec_period = tk.StringVar(value="2y")
        ttk.Combobox(top, textvariable=self.rec_period, values=PERIODS,
                     width=6, state="readonly").pack(side="left")
        ttk.Button(top, text="Rank", command=self.on_rank).pack(side="left", padx=12)

        frm = ttk.LabelFrame(parent, text="Ranking — best to long at top, best to short at bottom", padding=6)
        frm.pack(fill="both", expand=True, padx=12, pady=8)
        cols = ["Rank", "Ticker", *self.signal_names, "Composite", "Recommendation"]
        self.rec_table = ttk.Treeview(frm, columns=cols, show="headings", height=16)
        for c in cols:
            self.rec_table.heading(c, text=c)
            self.rec_table.column(c, width=110, anchor="center")
        self.rec_table.column("Rank", width=50, anchor="center")
        self.rec_table.column("Ticker", width=80, anchor="w")
        self.rec_table.column("Recommendation", width=130, anchor="center")
        self.rec_table.tag_configure("Long", background="#d6f5d6")
        self.rec_table.tag_configure("Short", background="#f9d6d6")
        self.rec_table.tag_configure("Neutral", background="#f0f0f0")
        xbar = ttk.Scrollbar(frm, orient="horizontal", command=self.rec_table.xview)
        self.rec_table.configure(xscrollcommand=xbar.set)
        self.rec_table.pack(fill="both", expand=True)
        xbar.pack(fill="x")

        foot = ttk.Frame(parent, padding=12)
        foot.pack(fill="x")
        ttk.Button(foot, text="Export Ranking CSV", command=self.on_export_ranking).pack(side="left")
        self.rec_status = tk.StringVar(
            value="Enter tickers (e.g. AAPL NVDA TSLA) or pick a universe, then Rank."
        )
        ttk.Label(foot, textvariable=self.rec_status, foreground="#666").pack(side="right")

    def on_rank(self) -> None:
        tickers = resolve(self.rec_input.get())
        if not tickers:
            self.rec_status.set("No tickers parsed — type symbols or pick a universe.")
            return
        self.rec_status.set(f"Ranking {len(tickers)} tickers…")
        self.update_idletasks()

        df = rank(tickers, period=self.rec_period.get())
        self._last_ranking = df

        for iid in self.rec_table.get_children():
            self.rec_table.delete(iid)
        for ticker, r in df.iterrows():
            rec = r["recommendation"]
            values = [int(r["rank"]), ticker]
            values += [_fmt(r.get(name)) for name in self.signal_names]
            values.append(_fmt(r["composite"]))
            values.append(rec)
            tag = rec if rec in {"Long", "Short", "Neutral"} else ""
            self.rec_table.insert("", "end", values=values, tags=(tag,))

        longs = (df["recommendation"] == "Long").sum()
        shorts = (df["recommendation"] == "Short").sum()
        self.rec_status.set(f"Ranked {len(df)} tickers — {longs} Long, {shorts} Short.")

    def on_export_ranking(self) -> None:
        if self._last_ranking is None or self._last_ranking.empty:
            messagebox.showinfo("Export Ranking", "Run a ranking first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="rankings.csv",
            initialdir="data", filetypes=[("CSV", "*.csv")],
        )
        if path:
            export_csv(self._last_ranking, path)
            self.rec_status.set(f"Wrote {path}")


def main() -> None:
    from core.env import load_local_keys
    load_local_keys()  # pick up API keys from .keys.env if present
    Dashboard().mainloop()


if __name__ == "__main__":
    main()
