"""FlexTrade — equity research desktop dashboard (Tkinter).

Run:
    python dashboard.py

Two tabs:
  * Single Ticker — search by name or symbol, see each person's signal on a
    common 1-10 scale plus their own rating + reasoning, a composite, and the
    stock's basic info (sector, industry, market cap, latest price).
  * Recommender   — rank a universe (indices / sectors / your own list) by the
    1-10 composite, best-to-long down to best-to-short, and export to CSV.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from core.env import load_local_keys
from core.recommender import rank
from core.registry import signal_specs
from core.runner import analyze_ticker, export_csv
from core.scoring import fmt_ten
from core.universe import available, resolve

PERIODS = ["6mo", "1y", "2y", "5y"]

# brand palette
NAVY = "#0B2545"
TEAL = "#1B9AAA"
LIGHT = "#F5F7FA"
GREEN = "#d6f5d6"
RED = "#f9d6d6"
GREY = "#eef0f2"


def _cap(mcap) -> str:
    if not mcap:
        return "N/A"
    mcap = float(mcap)
    if mcap >= 1e12:
        return f"${mcap/1e12:.2f}T"
    if mcap >= 1e9:
        return f"${mcap/1e9:.1f}B"
    if mcap >= 1e6:
        return f"${mcap/1e6:.0f}M"
    return f"${mcap:.0f}"


class Dashboard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FlexTrade — Equity Research")
        self.geometry("1180x860")
        self.configure(bg=LIGHT)

        self._init_style()
        self.specs = signal_specs()
        self.signal_names = [s["name"] for s in self.specs]
        self.rows: dict[str, dict] = {}
        self.category_trees: dict[str, ttk.Treeview] = {}
        self.last_report: dict | None = None
        self._last_ranking: pd.DataFrame | None = None

        self._banner()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        single = ttk.Frame(nb)
        rec = ttk.Frame(nb)
        nb.add(single, text="  Single Ticker  ")
        nb.add(rec, text="  Recommender  ")
        self._build_single(single)
        self._build_recommender(rec)

    # ---------- style / branding ----------
    def _init_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Helvetica", 11))
        style.configure("TNotebook", background=LIGHT, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Helvetica", 11, "bold"))
        style.map("TNotebook.Tab", background=[("selected", TEAL)],
                  foreground=[("selected", "white")])
        style.configure("Treeview", rowheight=24, fieldbackground="white")
        style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"))
        style.configure("TLabelframe.Label", font=("Helvetica", 11, "bold"), foreground=NAVY)
        style.configure("Accent.TButton", font=("Helvetica", 11, "bold"))

    def _banner(self) -> None:
        bar = tk.Frame(self, bg=NAVY, height=64)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        # simple logo mark + wordmark
        tk.Label(bar, text="▲", bg=NAVY, fg=TEAL, font=("Helvetica", 26, "bold")).pack(side="left", padx=(18, 0))
        tk.Label(bar, text="FlexTrade", bg=NAVY, fg="white",
                 font=("Helvetica", 22, "bold")).pack(side="left", padx=(6, 0))
        tk.Label(bar, text="Multi-Factor Equity Research", bg=NAVY, fg="#9fb3c8",
                 font=("Helvetica", 12, "italic")).pack(side="left", padx=14, pady=(14, 0))
        tk.Label(bar, text="every signal on a 1–10 scale", bg=NAVY, fg="#6b85a0",
                 font=("Helvetica", 10)).pack(side="right", padx=18)

    # ---------- Single Ticker tab ----------
    def _build_single(self, parent: ttk.Frame) -> None:
        # search / input row
        top = ttk.Frame(parent, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Search name or ticker:", font=("Helvetica", 12, "bold")).pack(side="left")
        self.search_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.search_var, width=22, font=("Helvetica", 13))
        e.pack(side="left", padx=8)
        e.bind("<Return>", lambda _e: self.on_search())
        e.focus()
        ttk.Button(top, text="Search", command=self.on_search).pack(side="left")
        ttk.Label(top, text="Period:").pack(side="left", padx=(14, 4))
        self.period_var = tk.StringVar(value="2y")
        ttk.Combobox(top, textvariable=self.period_var, values=PERIODS, width=6,
                     state="readonly").pack(side="left")
        ttk.Button(top, text="Analyze ▶", style="Accent.TButton",
                   command=self.on_analyze).pack(side="left", padx=12)
        self.composite_var = tk.StringVar(value="Composite: —")
        tk.Label(top, textvariable=self.composite_var, bg=LIGHT, fg=NAVY,
                 font=("Helvetica", 16, "bold")).pack(side="right")

        # search results (hidden until used) + stock info, side by side
        mid = ttk.Frame(parent, padding=(12, 0))
        mid.pack(fill="x")
        res_box = ttk.LabelFrame(mid, text="Search results", padding=6)
        res_box.pack(side="left", fill="y", padx=(0, 8))
        self.search_list = tk.Listbox(res_box, width=34, height=5,
                                      font=("Helvetica", 11), activestyle="none")
        self.search_list.pack(fill="both", expand=True)
        self.search_list.bind("<Double-Button-1>", self._pick_search)
        self.search_list.bind("<Return>", self._pick_search)

        self.info_box = ttk.LabelFrame(mid, text="Stock info", padding=8)
        self.info_box.pack(side="left", fill="both", expand=True)
        self.info_var = tk.StringVar(value="Search a company or enter a ticker, then Analyze.")
        tk.Label(self.info_box, textvariable=self.info_var, bg="white", fg="#222",
                 justify="left", anchor="nw", font=("Helvetica", 11), wraplength=620).pack(
            fill="both", expand=True)

        # category panels
        container = ttk.Frame(parent, padding=(12, 8))
        container.pack(fill="x")
        cats: list[str] = []
        for s in self.specs:
            if s["category"] not in cats:
                cats.append(s["category"])
        for cat in cats:
            panel = ttk.LabelFrame(container, text=cat, padding=6)
            panel.pack(side="left", fill="both", expand=True, padx=4)
            tree = ttk.Treeview(panel, columns=("owner", "score", "rating"),
                                show="tree headings", height=5)
            tree.heading("#0", text="Signal")
            tree.heading("owner", text="Owner")
            tree.heading("score", text="Score")
            tree.heading("rating", text="Rating")
            tree.column("#0", width=120)
            tree.column("owner", width=60, anchor="center")
            tree.column("score", width=66, anchor="center")
            tree.column("rating", width=110, anchor="center")
            tree.pack(fill="both", expand=True)
            tree.bind("<<TreeviewSelect>>", self._on_signal_select)
            for s in self.specs:
                if s["category"] == cat:
                    tree.insert("", "end", iid=s["name"], text=s["name"],
                                values=(s["owner"], "—", "—"))
            self.category_trees[cat] = tree

        # breakdown pane
        bd = ttk.LabelFrame(parent, text="How each rating was computed (click a signal to focus)", padding=6)
        bd.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.breakdown = tk.Text(bd, height=9, wrap="word", state="disabled",
                                 font=("Menlo", 10), bg="white")
        sb = ttk.Scrollbar(bd, orient="vertical", command=self.breakdown.yview)
        self.breakdown.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.breakdown.pack(side="left", fill="both", expand=True)

        # comparison table
        tbl = ttk.LabelFrame(parent, text="Compared tickers (1–10 per signal)", padding=6)
        tbl.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        cols = ["Ticker", *self.signal_names, "Composite", "Rating"]
        self.table = ttk.Treeview(tbl, columns=cols, show="headings", height=5)
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=92, anchor="center")
        self.table.column("Ticker", width=70, anchor="w")
        xb = ttk.Scrollbar(tbl, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=xb.set)
        self.table.pack(fill="both", expand=True)
        xb.pack(fill="x")

        foot = ttk.Frame(parent, padding=(12, 4))
        foot.pack(fill="x")
        ttk.Button(foot, text="Export CSV", command=self.on_export).pack(side="left")
        ttk.Button(foot, text="Clear", command=self.on_clear).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(foot, textvariable=self.status_var, foreground="#666").pack(side="right")

    # ---------- search ----------
    def on_search(self) -> None:
        """Search by company name OR symbol; show matches to pick from."""
        q = self.search_var.get().strip()
        if not q:
            return
        try:
            from modules.stock_info import search
            hits = search(q, 12)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Search failed: {exc.__class__.__name__}")
            return
        self.search_list.delete(0, "end")
        self._hits = hits
        for h in hits:
            self.search_list.insert("end", f"{h['ticker']:<7} {h['name'][:30]}")
        if hits:
            self.status_var.set(f"{len(hits)} match(es) — double-click one to analyze "
                                "(or type a ticker and hit Analyze).")
        else:
            self.status_var.set("No matches — try a different name, or type a ticker + Analyze.")

    def _pick_search(self, _e=None) -> None:
        sel = self.search_list.curselection()
        if not sel:
            return
        self.search_var.set(self._hits[sel[0]]["ticker"])
        self.on_analyze()

    # ---------- analyze ----------
    def on_analyze(self) -> None:
        # the entry is the single source of truth: a symbol typed or picked from search
        ticker = self.search_var.get().strip().upper()
        if not ticker:
            return
        self.status_var.set(f"Analyzing {ticker}…")
        self.update_idletasks()

        report = analyze_ticker(ticker, period=self.period_var.get())
        self.last_report = report
        self._show_info(ticker)

        for cat, tree in self.category_trees.items():
            for s in self.specs:
                if s["category"] != cat:
                    continue
                sig = report["signals"].get(s["name"], {})
                tree.item(s["name"], values=(s["owner"], sig.get("native_score", "—"),
                                             sig.get("native_rating", "—")))

        comp = report["composite"]
        comp_str = f"{comp:.1f} / 10" if isinstance(comp, (int, float)) else "—"
        self.composite_var.set(f"Composite: {comp_str}  ({report['composite_label']})")
        self._render_breakdown()

        store: dict = {}
        values = [ticker]
        for name in self.signal_names:
            ten = report["signals"].get(name, {}).get("ten")
            store[name] = ten
            values.append(fmt_ten(ten) if isinstance(ten, (int, float)) else "—")
        store["composite"] = comp
        values.append(comp_str)
        values.append(report["composite_label"])
        if ticker in self.rows:
            self.table.item(ticker, values=values)
        else:
            self.table.insert("", "end", iid=ticker, values=values)
        self.rows[ticker] = store
        self.status_var.set(f"Analyzed {ticker} — composite {comp_str} "
                            f"(avg of {report['n_scored']} signals).")

    def _show_info(self, ticker: str) -> None:
        try:
            from modules.stock_info import get_info, latest_ohlc
            info = get_info(ticker)
            bar = latest_ohlc(ticker)
        except Exception as exc:  # noqa: BLE001
            self.info_var.set(f"{ticker}: info unavailable ({exc.__class__.__name__})")
            return
        ohlc = (f"O {bar['o']}  H {bar['h']}  L {bar['l']}  C {bar['c']}"
                if bar else "price unavailable")
        desc = (info["description"] or "")[:280]
        self.info_var.set(
            f"{info['name']} ({info['ticker']})   ·   {info['exchange']}\n"
            f"Sector: {info['sector']}    Industry: {info['industry']}    "
            f"Market cap: {_cap(info['market_cap'])}\n"
            f"Latest: {ohlc}\n\n{desc}"
        )

    def _on_signal_select(self, event) -> None:
        sel = event.widget.selection()
        if sel:
            self._render_breakdown(only=sel[0])

    def _render_breakdown(self, only: str | None = None) -> None:
        report = self.last_report
        self.breakdown.configure(state="normal")
        self.breakdown.delete("1.0", "end")
        if report:
            self.breakdown.insert("end", f"{report['ticker']}\n")
            for name, sig in report["signals"].items():
                if only and name != only:
                    continue
                self.breakdown.insert("end", f"\n[{sig['owner']} · {name}]  "
                                             f"{sig['native_score']}  → {sig['native_rating']}\n")
                for line in sig.get("breakdown", []):
                    self.breakdown.insert("end", f"   • {line}\n")
        self.breakdown.configure(state="disabled")

    def on_export(self) -> None:
        if not self.rows:
            messagebox.showinfo("Export CSV", "Analyze at least one ticker first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="ratings.csv",
                                            initialdir="data", filetypes=[("CSV", "*.csv")])
        if path:
            df = pd.DataFrame.from_dict(self.rows, orient="index")
            df.index.name = "ticker"
            export_csv(df, path)
            self.status_var.set(f"Wrote {path}")

    def on_clear(self) -> None:
        for iid in self.table.get_children():
            self.table.delete(iid)
        self.rows.clear()
        self.status_var.set("Cleared.")

    # ---------- Recommender tab ----------
    def _build_recommender(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Universe:", font=("Helvetica", 12, "bold")).pack(side="left")
        self.rec_universe = tk.StringVar()
        combo = ttk.Combobox(top, textvariable=self.rec_universe, values=available(),
                             width=16, state="readonly")
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.rec_input.set(self.rec_universe.get()))
        ttk.Label(top, text="or tickers:").pack(side="left", padx=(10, 4))
        self.rec_input = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.rec_input, width=34, font=("Helvetica", 11))
        ent.pack(side="left")
        ent.bind("<Return>", lambda _e: self.on_rank())
        ttk.Label(top, text="Period:").pack(side="left", padx=(10, 4))
        self.rec_period = tk.StringVar(value="2y")
        ttk.Combobox(top, textvariable=self.rec_period, values=PERIODS, width=6,
                     state="readonly").pack(side="left")
        ttk.Button(top, text="Rank ▶", style="Accent.TButton", command=self.on_rank).pack(side="left", padx=12)

        hint = ttk.Label(parent, foreground="#777", padding=(12, 0),
                         text="Pick an index/sector (DOW30, TECH, ENERGY, …) or paste your own list. "
                              "Ranked by the 1–10 composite — top = best to long, bottom = best to short.")
        hint.pack(fill="x")

        frm = ttk.LabelFrame(parent, text="Ranking", padding=6)
        frm.pack(fill="both", expand=True, padx=12, pady=8)
        cols = ["Rank", "Ticker", *self.signal_names, "Composite", "Rating", "Call"]
        self.rec_table = ttk.Treeview(frm, columns=cols, show="headings", height=20)
        for c in cols:
            self.rec_table.heading(c, text=c)
            self.rec_table.column(c, width=84, anchor="center")
        self.rec_table.column("Rank", width=44)
        self.rec_table.column("Ticker", width=66, anchor="w")
        self.rec_table.column("Call", width=80)
        self.rec_table.tag_configure("Long", background=GREEN)
        self.rec_table.tag_configure("Short", background=RED)
        self.rec_table.tag_configure("Neutral", background=GREY)
        xb = ttk.Scrollbar(frm, orient="horizontal", command=self.rec_table.xview)
        yb = ttk.Scrollbar(frm, orient="vertical", command=self.rec_table.yview)
        self.rec_table.configure(xscrollcommand=xb.set, yscrollcommand=yb.set)
        yb.pack(side="right", fill="y")
        self.rec_table.pack(side="top", fill="both", expand=True)
        xb.pack(side="bottom", fill="x")

        foot = ttk.Frame(parent, padding=12)
        foot.pack(fill="x")
        ttk.Button(foot, text="Export Ranking CSV", command=self.on_export_ranking).pack(side="left")
        self.rec_status = tk.StringVar(value="Pick a universe and Rank.")
        ttk.Label(foot, textvariable=self.rec_status, foreground="#666").pack(side="right")

    def on_rank(self) -> None:
        tickers = resolve(self.rec_input.get() or self.rec_universe.get())
        if not tickers:
            self.rec_status.set("No tickers — pick a universe or paste symbols.")
            return
        self.rec_status.set(f"Ranking {len(tickers)} tickers… (this can take a moment)")
        self.update_idletasks()
        df = rank(tickers, period=self.rec_period.get())
        self._last_ranking = df

        for iid in self.rec_table.get_children():
            self.rec_table.delete(iid)
        for ticker, r in df.iterrows():
            rec = r["recommendation"]
            vals = [int(r["rank"]), ticker]
            vals += [fmt_ten(r.get(n)) if isinstance(r.get(n), (int, float)) else "—"
                     for n in self.signal_names]
            comp = r["composite_1_10"]
            vals.append(fmt_ten(comp) if isinstance(comp, (int, float)) else "—")
            vals.append(r["rating"])
            vals.append(rec)
            tag = rec if rec in {"Long", "Short", "Neutral"} else ""
            self.rec_table.insert("", "end", values=vals, tags=(tag,))
        longs = (df["recommendation"] == "Long").sum()
        shorts = (df["recommendation"] == "Short").sum()
        self.rec_status.set(f"Ranked {len(df)} — {longs} Long, {shorts} Short.")

    def on_export_ranking(self) -> None:
        if self._last_ranking is None or self._last_ranking.empty:
            messagebox.showinfo("Export Ranking", "Run a ranking first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="rankings.csv",
                                            initialdir="data", filetypes=[("CSV", "*.csv")])
        if path:
            export_csv(self._last_ranking, path)
            self.rec_status.set(f"Wrote {path}")


def main() -> None:
    load_local_keys()
    Dashboard().mainloop()


if __name__ == "__main__":
    main()
