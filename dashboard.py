"""FlexTrade — equity research desktop dashboard (Tkinter), bare-bones.

Run:
    python dashboard.py

Analysis runs on a background thread so the window never freezes; results are
cached so re-analyzing a ticker is instant. Drop a logo PNG at
assets/flextrade_logo.png to show the real FlexTrade mark; otherwise a simple
text logo is drawn.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from core.env import load_local_keys
from core.recommender import rank
from core.registry import signal_specs
from core.runner import analyze_ticker, export_csv
from core.scoring import fmt_ten, is_scored, signal_description
from core.universe import available, resolve

PERIODS = ["6mo", "1y", "2y", "5y"]
_LOGO = os.path.join(os.path.dirname(__file__), "assets", "flextrade_logo.png")


def _cap(mcap) -> str:
    if not mcap:
        return "N/A"
    mcap = float(mcap)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if mcap >= div:
            return f"${mcap/div:.1f}{suf}"
    return f"${mcap:.0f}"


class Dashboard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FlexTrade Equity Research Bot")
        self.geometry("1440x1080")

        self.specs = signal_specs()
        self.signal_names = [s["name"] for s in self.specs]
        self.rows: dict[str, dict] = {}
        self.category_trees: dict[str, ttk.Treeview] = {}
        self.last_report: dict | None = None
        self._last_ranking: pd.DataFrame | None = None
        self._q: queue.Queue = queue.Queue()
        self._busy = False

        self._logo_bar()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        single = ttk.Frame(nb)
        rec = ttk.Frame(nb)
        nb.add(single, text="Single Ticker")
        nb.add(rec, text="Recommender")
        self._build_single(single)
        self._build_recommender(rec)
        self.after(120, self._poll)

    # ---------- logo ----------
    def _logo_bar(self) -> None:
        bar = tk.Frame(self, bg="black", height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        if os.path.exists(_LOGO):
            try:
                img = tk.PhotoImage(file=_LOGO)
                while img.width() > 360:           # downscale big images
                    img = img.subsample(2)
                self._logo_img = img
                tk.Label(bar, image=img, bg="black").pack(side="left", padx=12)
                return
            except Exception:
                pass
        f = tk.Frame(bar, bg="black")
        f.pack(side="left", padx=14, pady=10)
        tk.Label(f, text="FLE", bg="black", fg="white", font=("Helvetica", 22, "bold")).pack(side="left")
        tk.Label(f, text="X", bg="black", fg="#3DCC4A", font=("Helvetica", 22, "bold")).pack(side="left")
        tk.Label(f, text="TRADE", bg="black", fg="white", font=("Helvetica", 22, "bold")).pack(side="left")
        tk.Label(bar, text="Trade your best.®", bg="black", fg="#aaaaaa",
                 font=("Helvetica", 12, "italic")).pack(side="left", padx=8)

    # ---------- Single Ticker ----------
    def _build_single(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Name or ticker:").pack(side="left")
        self.search_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.search_var, width=20)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda _e: self.on_search())
        e.focus()
        ttk.Button(top, text="Search", command=self.on_search).pack(side="left")
        ttk.Label(top, text="Period:").pack(side="left", padx=(12, 4))
        self.period_var = tk.StringVar(value="2y")
        ttk.Combobox(top, textvariable=self.period_var, values=PERIODS, width=6,
                     state="readonly").pack(side="left")
        self.analyze_btn = ttk.Button(top, text="Analyze", command=self.on_analyze)
        self.analyze_btn.pack(side="left", padx=8)
        self.composite_var = tk.StringVar(value="Composite: —")
        ttk.Label(top, textvariable=self.composite_var,
                  font=("Helvetica", 14, "bold")).pack(side="right")

        # info row: search results + stock info + summary
        mid = ttk.Frame(parent, padding=(8, 0))
        mid.pack(fill="x")
        rb = ttk.LabelFrame(mid, text="Search results", padding=4)
        rb.pack(side="left", fill="y", padx=(0, 6))
        self.search_list = tk.Listbox(rb, width=26, height=4)
        self.search_list.pack()
        self.search_list.bind("<Double-Button-1>", self._pick_search)
        self.search_list.bind("<Return>", self._pick_search)

        ib = ttk.LabelFrame(mid, text="Stock info", padding=4)
        ib.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.info = tk.Text(ib, height=7, wrap="word", state="disabled",
                            background="white", foreground="#1a1a1a", relief="flat")
        self.info.tag_configure("name", font=("Helvetica", 17, "bold"))
        self.info.tag_configure("ticker", font=("Helvetica", 13, "bold"), foreground="#1a5fb4")
        self.info.tag_configure("sub", foreground="#555", font=("Helvetica", 10))
        self.info.tag_configure("price", foreground="#1a5fb4", font=("Helvetica", 11, "bold"))
        self.info.tag_configure("desc", foreground="#333", font=("Helvetica", 9))
        self.info.pack(fill="both", expand=True)
        self._set_text(self.info, "Search a company or enter a ticker, then Analyze.")

        sm = ttk.LabelFrame(mid, text="Summary", padding=4)
        sm.pack(side="left", fill="both", expand=True)
        self.summary = tk.Text(sm, height=5, wrap="word", state="disabled",
                               background="white", foreground="#1a1a1a")
        self.summary.pack(fill="both", expand=True)

        # nested notebook: Charts | Signals
        sub = ttk.Notebook(parent)
        sub.pack(fill="both", expand=True, padx=8, pady=4)
        charts_tab = ttk.Frame(sub)
        signals_tab = ttk.Frame(sub)
        news_tab = ttk.Frame(sub)
        sub.add(charts_tab, text="Charts")
        sub.add(signals_tab, text="Signals")
        sub.add(news_tab, text="News")

        news_bar = ttk.Frame(news_tab, padding=(0, 2))
        news_bar.pack(fill="x")
        ttk.Button(news_bar, text="Open News Window ↗",
                   command=self.open_news_window).pack(side="left")
        ttk.Label(news_bar, text="  (descriptions + clickable article links)",
                  foreground="#777").pack(side="left")
        self.news_text = tk.Text(news_tab, wrap="word", state="disabled", height=12,
                                 background="white", foreground="#1a1a1a")
        nsb = ttk.Scrollbar(news_tab, orient="vertical", command=self.news_text.yview)
        self.news_text.configure(yscrollcommand=nsb.set)
        self.news_text.tag_configure("positive", foreground="#1e7d32", font=("Helvetica", 11, "bold"))
        self.news_text.tag_configure("negative", foreground="#b3261e", font=("Helvetica", 11, "bold"))
        self.news_text.tag_configure("neutral", foreground="#41484f", font=("Helvetica", 11, "bold"))
        self.news_text.tag_configure("meta", foreground="#5f6368")
        self.news_text.tag_configure("reason", foreground="#444444", font=("Helvetica", 10, "italic"))
        nsb.pack(side="right", fill="y")
        self.news_text.pack(side="left", fill="both", expand=True)
        self._set_text(self.news_text, "News + per-article sentiment appears here after Analyze.")

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(9.5, 4.4), dpi=100, facecolor="white")
        gs = self.fig.add_gridspec(1, 2, width_ratios=[2.2, 1])
        self.ax_price = self.fig.add_subplot(gs[0])
        self.ax_sig = self.fig.add_subplot(gs[1])
        for ax in (self.ax_price, self.ax_sig):
            ax.text(0.5, 0.5, "analyze a ticker to see charts", ha="center",
                    va="center", transform=ax.transAxes, color="#9aa0a6")
            ax.set_xticks([]); ax.set_yticks([])
        self.fig.tight_layout(pad=2.0)
        self.canvas = FigureCanvasTkAgg(self.fig, master=charts_tab)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        cont = ttk.Frame(signals_tab, padding=4)
        cont.pack(fill="x")
        cats: list[str] = []
        for s in self.specs:
            if s["category"] not in cats:
                cats.append(s["category"])
        for cat in cats:
            panel = ttk.LabelFrame(cont, text=cat, padding=4)
            panel.pack(side="left", fill="both", expand=True, padx=3)
            tree = ttk.Treeview(panel, columns=("score", "rating"),
                                show="tree headings", height=5)
            tree.heading("#0", text="Signal")
            tree.heading("score", text="Score")
            tree.heading("rating", text="Rating")
            tree.column("#0", width=124)
            tree.column("score", width=64, anchor="center")
            tree.column("rating", width=112, anchor="center")
            tree.pack(fill="both", expand=True)
            tree.bind("<<TreeviewSelect>>", self._on_signal_select)
            for s in self.specs:
                if s["category"] == cat:
                    tree.insert("", "end", iid=s["name"], text=s["name"],
                                values=("—", "—"))
            self.category_trees[cat] = tree

        bd = ttk.LabelFrame(signals_tab, text="How each rating was computed (click a signal)", padding=4)
        bd.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.breakdown = tk.Text(bd, height=8, wrap="word", state="disabled",
                                 background="white", foreground="#1a1a1a")
        self.breakdown.tag_configure("sig", font=("Helvetica", 11, "bold"))
        self.breakdown.tag_configure("how", foreground="#444444",
                                     font=("Helvetica", 10, "italic"), spacing3=2)
        sb = ttk.Scrollbar(bd, orient="vertical", command=self.breakdown.yview)
        self.breakdown.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.breakdown.pack(side="left", fill="both", expand=True)

        tb = ttk.LabelFrame(parent, text="Compared tickers (1-10 per signal)", padding=4)
        tb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        cols = ["Ticker", *self.signal_names, "Composite", "Rating"]
        self.table = ttk.Treeview(tb, columns=cols, show="headings", height=4)
        for c in cols:
            self.table.heading(c, text=c)
            self.table.column(c, width=78, anchor="center")
        self.table.column("Ticker", width=64, anchor="w")
        xb = ttk.Scrollbar(tb, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=xb.set)
        self.table.pack(fill="both", expand=True)
        xb.pack(fill="x")

        foot = ttk.Frame(parent, padding=8)
        foot.pack(fill="x")
        ttk.Button(foot, text="Export CSV", command=self.on_export).pack(side="left")
        ttk.Button(foot, text="Clear", command=self.on_clear).pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(foot, textvariable=self.status_var).pack(side="right")

    # ---------- helpers ----------
    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    # ---------- search ----------
    def on_search(self) -> None:
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
            self.search_list.insert("end", f"{h['ticker']:<7} {h['name'][:26]}")
        self.status_var.set(f"{len(hits)} match(es) — double-click one, or type a ticker + Analyze."
                            if hits else "No matches.")

    def _pick_search(self, _e=None) -> None:
        sel = self.search_list.curselection()
        if sel:
            self.search_var.set(self._hits[sel[0]]["ticker"])
            self.on_analyze()

    # ---------- analyze (threaded) ----------
    def on_analyze(self) -> None:
        ticker = self.search_var.get().strip().upper()
        if not ticker or self._busy:
            return
        self._busy = True
        self.analyze_btn.configure(state="disabled")
        self.status_var.set(f"Analyzing {ticker}…  (first time can take a moment)")
        period = self.period_var.get()

        def work():
            try:
                report = analyze_ticker(ticker, period=period)
            except Exception as exc:  # noqa: BLE001
                self._q.put(("error", f"{ticker}: {exc.__class__.__name__}"))
                return
            info, bars, news = None, [], []
            try:
                from core.massive import period_to_days
                from modules.stock_info import get_info, get_ohlc
                info = get_info(ticker)
                bars = get_ohlc(ticker, days=int(period_to_days(period) * 0.7))
            except Exception:  # noqa: BLE001
                pass
            try:
                from modules.raghav_news import get_news
                news = get_news(ticker, 15)
            except Exception:  # noqa: BLE001
                pass
            latest = bars[-1] if bars else None
            self._q.put(("analyze", ticker, report, (info, latest), bars, news))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "analyze":
                    self._apply_report(*msg[1:])
                elif msg[0] == "rank":
                    self._apply_ranking(msg[1])
                elif msg[0] == "error":
                    self.status_var.set(msg[1])
                    self._busy = False
                    self.analyze_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _apply_report(self, ticker: str, report: dict, info, bars=None, news=None) -> None:
        self.last_report = report
        self._show_info(ticker, info)
        self._draw_charts(ticker, report, bars or [], info[0] if info else None)
        self._show_news(news or [])
        for cat, tree in self.category_trees.items():
            for s in self.specs:
                if s["category"] != cat:
                    continue
                sig = report["signals"].get(s["name"], {})
                tree.item(s["name"], values=(sig.get("native_score", "—"),
                                             sig.get("native_rating", "—")))
        comp = report["composite"]
        comp_str = f"{comp:.1f}/10" if is_scored(comp) else "—"
        self.composite_var.set(f"Composite: {comp_str} ({report['composite_label']})")
        self._render_breakdown()

        store, values = {}, [ticker]
        for name in self.signal_names:
            ten = report["signals"].get(name, {}).get("ten")
            store[name] = ten if is_scored(ten) else None
            values.append(fmt_ten(ten) if is_scored(ten) else "—")
        store["composite"] = comp
        values += [comp_str, report["composite_label"]]
        if ticker in self.rows:
            self.table.item(ticker, values=values)
        else:
            self.table.insert("", "end", iid=ticker, values=values)
        self.rows[ticker] = store
        self.status_var.set(f"{ticker}: {comp_str} (avg of {report['n_scored']} signals).")
        self._busy = False
        self.analyze_btn.configure(state="normal")

    def _show_info(self, ticker: str, info) -> None:
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        if not info or not info[0]:
            self.info.insert("end", f"{ticker}: stock info unavailable.")
            self.info.configure(state="disabled")
            return
        i, bar = info
        self.info.insert("end", f"{i['name']}  ", ("name",))
        self.info.insert("end", f"{i['ticker']}\n", ("ticker",))
        self.info.insert("end", f"{i['exchange']}   ·   {i['sector']}   ·   {i['industry']}"
                                f"   ·   Cap {_cap(i['market_cap'])}\n", ("sub",))
        if bar:
            self.info.insert("end", f"O {bar['o']}   H {bar['h']}   L {bar['l']}   C {bar['c']}\n", ("price",))
        else:
            self.info.insert("end", "price n/a\n", ("price",))
        if i.get("description"):
            self.info.insert("end", "\n" + (i["description"] or "")[:360], ("desc",))
        self.info.configure(state="disabled")

    def _draw_charts(self, ticker: str, report: dict, bars: list, info_dict) -> None:
        from core.charts import draw_price, draw_signal_bars, summarize
        chg = draw_price(self.ax_price, bars, ticker, self.period_var.get())
        items = [(n, s["ten"]) for n, s in report["signals"].items() if is_scored(s["ten"])]
        draw_signal_bars(self.ax_sig, items, report["composite"])
        try:
            self.fig.tight_layout(pad=2.0)
        except Exception:  # noqa: BLE001
            pass
        self.canvas.draw_idle()
        self._set_text(self.summary, summarize(report, info_dict, chg))

    @staticmethod
    def _group_news(news: list) -> list[tuple[str, list]]:
        """Bucket articles by sentiment. Positive & negative first (the most
        informative), ordered by count so the bigger pile is on top; neutral last.
        """
        groups: dict[str, list] = {"positive": [], "negative": [], "neutral": []}
        for n in news:
            sent = (n.get("sentiment") or "neutral").lower()
            groups[sent if sent in groups else "neutral"].append(n)
        informative = sorted(
            [("positive", groups["positive"]), ("negative", groups["negative"])],
            key=lambda kv: len(kv[1]), reverse=True)
        return informative + [("neutral", groups["neutral"])]

    @staticmethod
    def _relevance_line(n: dict, ticker: str) -> str:
        """A small line on why this article is relevant to the company.

        Prefer the model's per-article reasoning; if missing, fall back to a
        generic line so every article always carries a relevance note.
        """
        reason = (n.get("reasoning") or "").strip()
        if reason:
            return f"Why it matters for {ticker}: {reason}"
        sent = (n.get("sentiment") or "neutral").lower()
        return (f"Why it matters for {ticker}: {ticker} is a named subject of this "
                f"article; tagged {sent} for {ticker}.")

    def _news_tally(self, groups: list[tuple[str, list]]) -> str:
        counts = {s: len(items) for s, items in groups}
        return (f"Positive {counts.get('positive', 0)}   ·   "
                f"Negative {counts.get('negative', 0)}   ·   "
                f"Neutral {counts.get('neutral', 0)}")

    def _show_news(self, news: list) -> None:
        self._last_news = news
        ticker = self.last_report["ticker"] if self.last_report else ""
        self._last_news_ticker = ticker
        self.news_text.configure(state="normal")
        self.news_text.delete("1.0", "end")
        if not news:
            self.news_text.insert("end", "No recent news found for this ticker.")
            self.news_text.configure(state="disabled")
            return
        groups = self._group_news(news)
        self.news_text.insert("end", self._news_tally(groups) + "\n\n", ("meta",))
        for sent, items in groups:
            if not items:
                continue
            self.news_text.insert("end", f"{sent.upper()}  ({len(items)})\n", (sent,))
            for n in items:
                self.news_text.insert("end", f"   • {n.get('title', '')}\n")
                self.news_text.insert("end", f"      {n.get('publisher', '')} · {n.get('published', '')}\n", ("meta",))
                self.news_text.insert("end",
                                      f"      {self._relevance_line(n, ticker)}\n",
                                      ("reason",))
            self.news_text.insert("end", "\n")
        self.news_text.configure(state="disabled")

    def open_news_window(self) -> None:
        """Open a separate, richer news window: sentiment, description, link."""
        news = getattr(self, "_last_news", None)
        if not news:
            messagebox.showinfo("News", "Analyze a ticker first.")
            return
        ticker = getattr(self, "_last_news_ticker", "")
        win = tk.Toplevel(self)
        win.title(f"FlexTrade News — {ticker}")
        win.geometry("760x720")

        txt = tk.Text(win, wrap="word", state="disabled", padx=12, pady=10,
                      font=("Helvetica", 11), cursor="arrow")
        bar = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.configure(background="white", foreground="#1a1a1a")
        txt.tag_configure("title", font=("Helvetica", 15, "bold"), foreground="#111111")
        txt.tag_configure("section", font=("Helvetica", 13, "bold"), spacing1=8, spacing3=4)
        txt.tag_configure("h", font=("Helvetica", 12, "bold"), foreground="#1a1a1a")
        txt.tag_configure("positive", foreground="#1e7d32")
        txt.tag_configure("negative", foreground="#b3261e")
        txt.tag_configure("neutral", foreground="#41484f")
        txt.tag_configure("meta", foreground="#5f6368", font=("Helvetica", 10))
        txt.tag_configure("desc", foreground="#222222")
        txt.tag_configure("reason", foreground="#444444", font=("Helvetica", 10, "italic"))
        txt.tag_configure("link", foreground="#1a5fb4", underline=True)

        groups = self._group_news(news)
        txt.configure(state="normal")
        txt.insert("end", f"Recent news for {ticker}\n", ("title",))
        txt.insert("end", self._news_tally(groups) + "\n", ("meta",))
        link_i = 0
        for sent, items in groups:
            if not items:
                continue
            txt.insert("end", f"\n{sent.upper()}  ({len(items)})\n", ("section", sent))
            for n in items:
                txt.insert("end", f"{n.get('title', '')}\n", ("h",))
                meta = n.get("publisher", "")
                if n.get("author"):
                    meta += f" · {n['author']}"
                meta += f" · {n.get('published', '')}"
                txt.insert("end", meta + "\n", ("meta",))
                if n.get("description"):
                    txt.insert("end", n["description"].strip() + "\n", ("desc",))
                txt.insert("end", self._relevance_line(n, ticker) + "\n", ("reason",))
                url = n.get("url")
                if url:
                    linktag = f"link{link_i}"
                    link_i += 1
                    txt.insert("end", "Open article ↗", ("link", linktag))
                    txt.tag_bind(linktag, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
                    txt.tag_bind(linktag, "<Enter>", lambda _e: txt.configure(cursor="hand2"))
                    txt.tag_bind(linktag, "<Leave>", lambda _e: txt.configure(cursor="arrow"))
                    txt.insert("end", "\n")
                txt.insert("end", "\n")
        txt.configure(state="disabled")

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
                self.breakdown.insert("end", f"\n[{name}]  {sig['native_score']} → "
                                             f"{sig['native_rating']}\n", ("sig",))
                how = signal_description(name)
                if how:
                    self.breakdown.insert("end", f"How it's computed: {how}\n", ("how",))
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

    # ---------- Recommender (threaded) ----------
    def _build_recommender(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Universe:").pack(side="left")
        self.rec_universe = tk.StringVar()
        combo = ttk.Combobox(top, textvariable=self.rec_universe, values=available(),
                             width=16, state="readonly")
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda _e: self.rec_input.set(self.rec_universe.get()))
        ttk.Label(top, text="or type anything:").pack(side="left", padx=(8, 4))
        self.rec_input = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.rec_input, width=34)
        ent.pack(side="left")
        ent.bind("<Return>", lambda _e: self.on_rank())
        ttk.Label(top, text="Period:").pack(side="left", padx=(8, 4))
        self.rec_period = tk.StringVar(value="2y")
        ttk.Combobox(top, textvariable=self.rec_period, values=PERIODS, width=6,
                     state="readonly").pack(side="left")
        self.rank_btn = ttk.Button(top, text="Rank", command=self.on_rank)
        self.rank_btn.pack(side="left", padx=8)

        ttk.Label(parent, padding=(8, 0), foreground="#555",
                  text="Type an index (SP500), a sector (tech, healthcare, energy…), or paste "
                       "tickers — constituents are pulled live from the web. Ranked by the "
                       "1-10 composite (top = best to long, bottom = best to short).").pack(fill="x")

        frm = ttk.LabelFrame(parent, text="Ranking", padding=4)
        frm.pack(fill="both", expand=True, padx=8, pady=6)
        cols = ["Rank", "Ticker", *self.signal_names, "Composite", "Rating", "Call"]
        self.rec_table = ttk.Treeview(frm, columns=cols, show="headings", height=20)
        for c in cols:
            self.rec_table.heading(c, text=c)
            self.rec_table.column(c, width=74, anchor="center")
        self.rec_table.column("Rank", width=40)
        self.rec_table.column("Ticker", width=60, anchor="w")
        self.rec_table.tag_configure("Long", background="#d6f5d6")
        self.rec_table.tag_configure("Short", background="#f9d6d6")
        yb = ttk.Scrollbar(frm, orient="vertical", command=self.rec_table.yview)
        xb = ttk.Scrollbar(frm, orient="horizontal", command=self.rec_table.xview)
        self.rec_table.configure(yscrollcommand=yb.set, xscrollcommand=xb.set)
        yb.pack(side="right", fill="y")
        self.rec_table.pack(side="top", fill="both", expand=True)
        xb.pack(side="bottom", fill="x")

        foot = ttk.Frame(parent, padding=8)
        foot.pack(fill="x")
        ttk.Button(foot, text="Export Ranking CSV", command=self.on_export_ranking).pack(side="left")
        self.rec_status = tk.StringVar(value="Pick a universe and Rank.")
        ttk.Label(foot, textvariable=self.rec_status).pack(side="right")

    def on_rank(self) -> None:
        tickers = resolve(self.rec_input.get() or self.rec_universe.get())
        if not tickers or self._busy:
            if not tickers:
                self.rec_status.set("No tickers — pick a universe or paste symbols.")
            return
        self._busy = True
        self.rank_btn.configure(state="disabled")
        self.rec_status.set(f"Ranking {len(tickers)} tickers… (first run is slowest)")
        period = self.rec_period.get()

        def work():
            try:
                df = rank(tickers, period=period)
                self._q.put(("rank", df))
            except Exception as exc:  # noqa: BLE001
                self._q.put(("error", f"Rank failed: {exc.__class__.__name__}"))
        threading.Thread(target=work, daemon=True).start()

    def _apply_ranking(self, df: pd.DataFrame) -> None:
        self._last_ranking = df
        for iid in self.rec_table.get_children():
            self.rec_table.delete(iid)
        for ticker, r in df.iterrows():
            rec = r["recommendation"]
            vals = [int(r["rank"]), ticker]
            vals += [fmt_ten(r.get(n)) if is_scored(r.get(n)) else "—"
                     for n in self.signal_names]
            comp = r["composite_1_10"]
            vals += [fmt_ten(comp) if is_scored(comp) else "—", r["rating"], rec]
            self.rec_table.insert("", "end", values=vals,
                                  tags=(rec if rec in {"Long", "Short"} else "",))
        longs = (df["recommendation"] == "Long").sum()
        shorts = (df["recommendation"] == "Short").sum()
        self.rec_status.set(f"Ranked {len(df)} — {longs} Long, {shorts} Short.")
        self._busy = False
        self.rank_btn.configure(state="normal")

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
