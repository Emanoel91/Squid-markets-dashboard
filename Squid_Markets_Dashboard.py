"""
Squid (QUID) Markets Dashboard
Data source: https://defillama.com/api/public/markets/squid-3

Run:
    pip install streamlit pandas numpy requests plotly
    streamlit run squid_markets_dashboard.py
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ─────────────────────────────── Config ───────────────────────────────
API_URL = "https://defillama.com/api/public/markets/squid-3"
LOGO_URL = "https://assets.coingecko.com/coins/images/102174404/standard/squid.jpg?1783587866"
TOP_N = 15
DEFAULT_BANDS = [0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.025, 0.05, 0.1, 0.25]

# Daily history (written by collect_daily_snapshot.py at the end of each UTC day).
# - Local file:   data/squid_daily_history.csv
# - Or a raw URL: set env var SQUID_HISTORY_URL, e.g.
#   https://raw.githubusercontent.com/<user>/<repo>/main/data/squid_daily_history.csv
HISTORY_PATH = Path("data/squid_daily_history.csv")
HISTORY_URL = os.environ.get("SQUID_HISTORY_URL", "")
TOP_PAIRS_HISTORY = 6

st.set_page_config(
    page_title="Squid (QUID) Markets Dashboard",
    page_icon=LOGO_URL,
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapsedControl"] {display: none;}
    .block-container {padding-top: 2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

PALETTE = px.colors.qualitative.Set2
GREEN, RED = "#54a24b", "#e45756"


# ─────────────────────────────── Helpers ──────────────────────────────
@st.cache_data(ttl=600, show_spinner="Fetching data from DefiLlama...")
def fetch(url: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://defillama.com/",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def nested(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def pct(x):
    return x * 100 if isinstance(x, (int, float)) else None


def fmt_usd(n, decimals=2):
    if n is None or pd.isna(n):
        return "—"
    a = abs(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"${n / div:,.{decimals}f}{suf}"
    return f"${n:,.{decimals}f}"


def fmt_quote_price(p, quote):
    if p is None or pd.isna(p):
        return "—"
    s = f"{p:,.2f}" if p >= 1 else f"{p:.8f}".rstrip("0")
    return f"{s} {quote}"


def top_n_with_others(s: pd.Series, n=10):
    s = s.sort_values(ascending=False)
    if len(s) <= n:
        return s
    top = s.iloc[:n].copy()
    top["Others"] = s.iloc[n:].sum()
    return top


def depth_at(arr, bands, band):
    """USD depth at the chosen distance band (arrays align with `depth_bands`)."""
    if not isinstance(arr, list) or not arr:
        return np.nan
    i = int(np.argmin([abs(b - band) for b in bands]))
    if i >= len(arr) or arr[i] is None:
        return np.nan
    return float(arr[i])


def parse_markets(data):
    rows = []
    for venue in ("cex", "dex"):
        block = data.get(venue) or {}
        if not isinstance(block, dict):
            continue
        for segment, items in block.items():
            for m in items or []:
                rows.append({
                    "exchange": m.get("display_name") or m.get("slug") or "Unknown",
                    "venue": (m.get("type") or venue).upper(),
                    "segment": m.get("market_type") or segment,
                    "pair": (m.get("symbol") or m.get("pair_id") or "N/A").upper(),
                    "quote": (m.get("quote") or "").upper(),
                    "price": m.get("price"),
                    "best_bid": m.get("best_bid"),
                    "best_ask": m.get("best_ask"),
                    "price_chg": pct(m.get("price_change_24h")),
                    "volume": m.get("volume_24h"),
                    "volume_prev": m.get("volume_prev_24h"),
                    "vol_chg": pct(m.get("volume_change_24h")),
                    "vol_normalized": nested(m, "nvol", "normalized"),
                    "vol_status": nested(m, "nvol", "status"),
                    "maker_fee": pct(m.get("maker_fee")),
                    "taker_fee": pct(m.get("taker_fee")),
                    "depth_bid": m.get("depth_bid_usd"),
                    "depth_ask": m.get("depth_ask_usd"),
                    "pair_url": m.get("pair_url"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    num_cols = ["price", "best_bid", "best_ask", "price_chg", "volume", "volume_prev",
                "vol_chg", "vol_normalized", "maker_fee", "taker_fee"]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = df["volume"].fillna(0)
    mid = (df["best_bid"] + df["best_ask"]) / 2
    valid = (df["best_bid"] > 0) & (df["best_ask"] >= df["best_bid"])
    df["spread_pct"] = np.where(valid, (df["best_ask"] - df["best_bid"]) / mid * 100, np.nan)
    df["market"] = df["exchange"] + " · " + df["pair"]
    return df


# ─────────────────────────── Daily history ────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def load_history(url: str, path: str) -> pd.DataFrame:
    cols = ["date", "exchange", "pair", "volume", "volume_prev"]
    try:
        if url:
            h = pd.read_csv(url)
        elif Path(path).exists():
            h = pd.read_csv(path)
        else:
            return pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in h.columns:
            h[c] = np.nan
    h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.normalize()
    for c in ("volume", "volume_prev"):
        h[c] = pd.to_numeric(h[c], errors="coerce")
    return h.dropna(subset=["date"])[cols]


def stacked_over_time(d, key, title, normalized=False, order=None):
    """Daily stacked bar chart (absolute or 100% normalized)."""
    g = d.groupby(["day", key], as_index=False)["volume"].sum()
    orders = {"day": sorted(g["day"].unique())}
    if order:
        orders[key] = order
    fig = px.bar(
        g, x="day", y="volume", color=key, title=title,
        color_discrete_sequence=PALETTE, category_orders=orders,
    )
    fig.update_layout(
        barmode="stack", height=430, xaxis_title=None, xaxis_type="category",
        legend=dict(orientation="h", y=-0.2, title=None),
    )
    if normalized:
        fig.update_layout(barnorm="percent", yaxis_title="Share of daily volume (%)",
                          yaxis_ticksuffix="%")
    else:
        fig.update_layout(yaxis_title="Volume (USD)")
    return fig


# ─────────────────────────────── Load data ────────────────────────────
try:
    data = fetch(API_URL)
except Exception as e:
    st.error(f"Could not fetch the API: {e}")
    st.info("Open the API URL in your browser, save the JSON, and upload it below.")
    uploaded = st.file_uploader("Upload JSON", type="json")
    if uploaded is None:
        st.stop()
    data = json.load(uploaded)

f = parse_markets(data)
if f.empty:
    st.error("No markets were found in the API response.")
    st.stop()

bands = data.get("depth_bands") or DEFAULT_BANDS

# ─────────────────────────────── Aggregates ───────────────────────────
total_vol = f["volume"].sum()
total_prev = f["volume_prev"].sum(min_count=1)
f["share"] = f["volume"] / total_vol * 100 if total_vol > 0 else 0.0

ex_agg = (
    f.groupby("exchange")
    .agg(
        volume=("volume", "sum"),
        volume_prev=("volume_prev", lambda s: s.sum(min_count=1)),
        markets=("pair", "count"),
        pairs=("pair", "nunique"),
    )
    .reset_index()
)
ex_agg["vol_chg"] = np.where(
    ex_agg["volume_prev"] > 0, (ex_agg["volume"] / ex_agg["volume_prev"] - 1) * 100, np.nan
)
ex_agg["share"] = ex_agg["volume"] / total_vol * 100 if total_vol > 0 else 0.0
ex_agg = ex_agg.sort_values("volume", ascending=False).reset_index(drop=True)

pair_agg = (
    f.groupby("pair")
    .agg(volume=("volume", "sum"), exchanges=("exchange", "nunique"))
    .reset_index()
    .sort_values("volume", ascending=False)
    .reset_index(drop=True)
)
pair_agg["share"] = pair_agg["volume"] / total_vol * 100 if total_vol > 0 else 0.0

quote_agg = (
    f[f["quote"] != ""].groupby("quote")["volume"].sum().reset_index()
    .sort_values("volume", ascending=False)
)

top1 = ex_agg["share"].iloc[0] if len(ex_agg) else 0
top3 = ex_agg["share"].head(3).sum()

# ── Daily history: recorded snapshots + today's live snapshot (until it is recorded)
hist = load_history(HISTORY_URL, str(HISTORY_PATH))
today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
live = f[["exchange", "pair", "volume", "volume_prev"]].assign(date=today)
today_recorded = (not hist.empty) and bool((hist["date"] == today).any())
if today_recorded:
    hist_all = hist.copy()
elif hist.empty:
    hist_all = live.copy()
else:
    hist_all = pd.concat([hist, live], ignore_index=True)
hist_all["date"] = pd.to_datetime(hist_all["date"])
hist_all["day"] = hist_all["date"].dt.strftime("%Y-%m-%d")
n_recorded_days = hist["date"].nunique() if not hist.empty else 0

seg = nested(data, "segments", "spot") or next(iter((data.get("segments") or {}).values()), {}) or {}
price_usd = seg.get("price")
price_chg = pct(seg.get("price_change_24h"))

# ─────────────────────────────── Header ───────────────────────────────
logo_col, title_col = st.columns([1, 14], vertical_alignment="center")
with logo_col:
    st.image(LOGO_URL, width=64)
with title_col:
    st.title("Squid (QUID) — Markets Analytics Dashboard")

last_updated = data.get("last_updated")
if last_updated:
    try:
        last_updated = pd.to_datetime(last_updated).strftime("%Y-%m-%d")
    except Exception:
        last_updated = str(last_updated)[:10]
    st.caption(f"last_updated: {last_updated}")

st.markdown(
    "[🌐 Squid Website](https://www.squidrouter.com/) &nbsp;&nbsp;|&nbsp;&nbsp; "
    "[🐦 Squid X Account](https://x.com/squidrouter)"
)

# ─────────────────────────────── KPIs ─────────────────────────────────
CARD_COLOR = "#e6fb34"
NEUTRAL_SUB = "#5f6b00"
POS_COLOR = "#16a34a"   # green for positive change
NEG_COLOR = "#dc2626"   # red for negative change


def kpi_card(col, label: str, value: str, sub: str = None, sub_color: str = NEUTRAL_SUB):
    """Bold KPI card with a #e6fb34 colour pattern; `sub` is the footnote under the value."""
    sub_html = (f'<div style="font-size:14px; font-weight:700; color:{sub_color}; margin-top:2px;">{sub}</div>'
                if sub else '<div style="font-size:14px; margin-top:2px;">&nbsp;</div>')
    col.markdown(
        f"""
<div style="background:linear-gradient(180deg,#fcffe6 0%,#f4fda6 100%); border:1.5px solid {CARD_COLOR};
            border-left:6px solid {CARD_COLOR}; border-radius:14px; padding:14px 16px; margin-bottom:14px;
            box-shadow:0 2px 8px rgba(230,251,52,0.35); min-height:118px;">
  <div style="font-size:14px; font-weight:700; color:{NEUTRAL_SUB};">{label}</div>
  <div style="font-size:28px; font-weight:800; color:#1a1a1a; line-height:1.25; word-break:break-word;">{value}</div>
  {sub_html}
</div>
""",
        unsafe_allow_html=True,
    )


def change_sub(chg, label: str):
    """Footnote text + colour for a % change: green if positive, red if negative."""
    if chg is None or pd.isna(chg):
        return "N/A", "#6b7280"
    arrow = "▲" if chg > 0 else "▼" if chg < 0 else "●"
    color = POS_COLOR if chg > 0 else NEG_COLOR if chg < 0 else "#6b7280"
    return f"{arrow} {chg:+.2f}% vs {label}", color


vol_chg_total = (
    (total_vol / total_prev - 1) * 100 if pd.notna(total_prev) and total_prev > 0 else None
)
top3_names = ", ".join(ex_agg["exchange"].head(3).tolist())

# Row 1 — Price / Volume (with change footnotes) / Exchanges
r1 = st.columns(3)
txt, clr = change_sub(price_chg, "24h ago")
kpi_card(r1[0], "QUID Price (USD)", f"${price_usd:,.4f}" if price_usd else "N/A", txt, clr)
txt, clr = change_sub(vol_chg_total, "previous 24h")
kpi_card(r1[1], "Total Volume (24h)", fmt_usd(total_vol), txt, clr)
kpi_card(r1[2], "Exchanges", f"{f['exchange'].nunique():,}", f"{len(f):,} listed markets")

# Row 2 — Pairs / concentration
r2 = st.columns(3)
kpi_card(r2[0], "Unique Pairs", f"{f['pair'].nunique():,}",
         f"Top pair: {pair_agg['pair'].iloc[0]}" if len(pair_agg) else None)
kpi_card(r2[1], "Top Exchange Share", f"{top1:.1f}%", f"🏆 {ex_agg['exchange'].iloc[0]}")
kpi_card(r2[2], "Top-3 Exchanges Share", f"{top3:.1f}%", top3_names)

st.divider()

# ════════════════════════════ Overview ═══════════════════════════════
st.header("Market Overview")
c1, c2 = st.columns([3, 2])

with c1:
    d = ex_agg.head(TOP_N).sort_values("volume")
    fig = px.bar(
        d, x="volume", y="exchange", orientation="h",
        text=d["volume"].map(fmt_usd), color="volume",
        color_continuous_scale="Viridis",
        title=f"Top {min(TOP_N, len(ex_agg))} Exchanges by 24h Volume",
    )
    fig.update_layout(coloraxis_showscale=False, yaxis_title=None,
                      xaxis_title="Volume (USD)", height=480)
    fig.update_traces(textposition="outside", cliponaxis=False)
    st.plotly_chart(fig, use_container_width=True)

with c2:
    s = top_n_with_others(ex_agg.set_index("exchange")["volume"], 10)
    fig = px.pie(
        names=s.index, values=s.values, hole=0.55,
        color_discrete_sequence=PALETTE, title="24h Volume Share by Exchange",
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(height=480, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

c1, c2 = st.columns(2)
ex_order = ex_agg["exchange"].tolist()
with c1:
    st.plotly_chart(
        stacked_over_time(hist_all, "exchange", "Trading Volume by Exchange Over Time",
                          normalized=False, order=ex_order),
        use_container_width=True,
    )
with c2:
    st.plotly_chart(
        stacked_over_time(hist_all, "exchange", "Normalized Trading Volume by Exchange Over Time",
                          normalized=True, order=ex_order),
        use_container_width=True,
    )
st.caption(
    f"Daily history: {n_recorded_days} recorded day(s). Each day's 24h volume is recorded at the end of "
    "the day by `collect_daily_snapshot.py`; until then the latest bar shows today's live 24h snapshot."
)

tm = f[f["volume"] > 0]
if not tm.empty:
    fig = px.treemap(
        tm, path=["exchange", "pair"], values="volume",
        color="volume", color_continuous_scale="Tealgrn",
        title="24h Volume Treemap (Exchange → Pair)",
    )
    fig.update_traces(
        texttemplate="<b>%{label}</b><br>%{value:$,.0f}",
        textposition="middle center",
    )
    fig.update_layout(height=450, coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ════════════════════════════ Exchanges ══════════════════════════════
st.header("Exchange Analysis")
c1, c2 = st.columns(2)
with c1:
    d = ex_agg.head(TOP_N)
    fig = px.bar(
        d, x="exchange", y="markets", color="pairs",
        color_continuous_scale="Blues",
        title="Listed Markets per Exchange (color = unique pairs)",
    )
    fig.update_layout(xaxis_title=None, yaxis_title="Markets", height=420)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    fig = px.scatter(
        ex_agg, x="markets", y="volume", size="volume", color="share",
        hover_name="exchange", color_continuous_scale="Turbo", size_max=55,
        log_y=bool((ex_agg["volume"] > 0).all()),
        title="Liquidity Map — Markets vs 24h Volume (bubble = volume)",
    )
    fig.update_layout(height=420, yaxis_title="Volume (USD, log)")
    st.plotly_chart(fig, use_container_width=True)

c3, c4 = st.columns(2)
with c3:
    d = ex_agg.melt(
        id_vars="exchange", value_vars=["volume_prev", "volume"],
        var_name="period", value_name="usd",
    )
    d["period"] = d["period"].map({"volume_prev": "Previous 24h", "volume": "Last 24h"})
    fig = px.bar(
        d, x="exchange", y="usd", color="period", barmode="group",
        color_discrete_map={"Previous 24h": "#9ecae9", "Last 24h": "#4c78a8"},
        title="Volume Momentum — Last 24h vs Previous 24h",
    )
    fig.update_layout(height=420, xaxis_title=None, yaxis_title="Volume (USD)",
                      legend=dict(orientation="h", y=1.12, title=None))
    st.plotly_chart(fig, use_container_width=True)
with c4:
    q = f[f["vol_normalized"].notna()].copy()
    if not q.empty:
        q = q.melt(
            id_vars="market", value_vars=["volume", "vol_normalized"],
            var_name="kind", value_name="usd",
        )
        q["kind"] = q["kind"].map({"volume": "Reported", "vol_normalized": "Normalized"})
        fig = px.bar(
            q, x="market", y="usd", color="kind", barmode="group",
            color_discrete_map={"Reported": "#f58518", "Normalized": "#4c78a8"},
            title="Volume Quality — Reported vs Normalized (24h)",
        )
        fig.update_layout(height=420, xaxis_title=None, yaxis_title="Volume (USD)",
                          legend=dict(orientation="h", y=1.12, title=None))
        st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Normalized volume is the API's adjusted volume figure (`nvol`). Where it is lower than the "
    "reported volume, reported volume may be high relative to the market's order-book liquidity."
)

c5, c6 = st.columns(2)
with c5:
    vc = ex_agg[ex_agg["vol_chg"].notna()].sort_values("vol_chg", ascending=False).copy()
    if not vc.empty:
        vc["direction"] = np.where(vc["vol_chg"] >= 0, "Increase", "Decrease")
        fig = px.bar(
            vc, x="exchange", y="vol_chg", color="direction",
            color_discrete_map={"Increase": GREEN, "Decrease": RED},
            text=vc["vol_chg"].map(lambda v: f"{v:+.1f}%"),
            title="Volume Change by Exchange (24h)",
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=420, xaxis_title=None, yaxis_title="Change vs previous 24h (%)",
                          showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
with c6:
    chg = (
        hist_all.groupby(["day", "exchange"])
        .agg(volume=("volume", "sum"), prev=("volume_prev", lambda s: s.sum(min_count=1)))
        .reset_index()
    )
    chg["vol_chg"] = np.where(chg["prev"] > 0, (chg["volume"] / chg["prev"] - 1) * 100, np.nan)
    chg = chg.dropna(subset=["vol_chg"])
    if not chg.empty:
        fig = px.line(
            chg, x="day", y="vol_chg", color="exchange", markers=True,
            color_discrete_sequence=PALETTE,
            category_orders={"day": sorted(chg["day"].unique()), "exchange": ex_order},
            title="Volume Change by Exchange over Time",
        )
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(height=420, xaxis_title=None, xaxis_type="category",
                          yaxis_title="Change vs previous 24h (%)",
                          legend=dict(orientation="h", y=-0.2, title=None))
        st.plotly_chart(fig, use_container_width=True)

st.subheader("Exchange Summary")
st.dataframe(
    ex_agg.rename(columns={
        "exchange": "Exchange", "volume": "Volume 24h (USD)", "volume_prev": "Prev. 24h (USD)",
        "vol_chg": "Volume Δ 24h %", "markets": "Markets", "pairs": "Unique Pairs",
        "share": "Share %",
    })[["Exchange", "Volume 24h (USD)", "Prev. 24h (USD)", "Volume Δ 24h %",
        "Markets", "Unique Pairs", "Share %"]],
    use_container_width=True, hide_index=True,
    column_config={
        "Volume 24h (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Prev. 24h (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Volume Δ 24h %": st.column_config.NumberColumn(format="%+.2f%%"),
        "Share %": st.column_config.ProgressColumn(format="%.2f%%", min_value=0, max_value=100),
    },
)

st.divider()

# ═════════════════════════════ Pairs ═════════════════════════════════
st.header("Pair & Quote Currency Analysis")
c1, c2 = st.columns(2)
with c1:
    d = pair_agg.head(TOP_N).sort_values("volume")
    fig = px.bar(
        d, x="volume", y="pair", orientation="h", color="exchanges",
        color_continuous_scale="Sunset", text=d["volume"].map(fmt_usd),
        title=f"Top {min(TOP_N, len(pair_agg))} Pairs by 24h Volume (color = # exchanges)",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(yaxis_title=None, xaxis_title="Volume (USD)", height=480)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    s = top_n_with_others(pair_agg.set_index("pair")["volume"], 10)
    fig = px.pie(
        names=s.index, values=s.values, hole=0.5,
        color_discrete_sequence=px.colors.qualitative.Pastel,
        title="24h Volume Share by Pair",
    )
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(height=480, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

# Top pairs over time (top N by total volume in the history window, rest grouped as "Others")
pairs_hist = hist_all.copy()
top_pairs = (
    pairs_hist.groupby("pair")["volume"].sum().nlargest(TOP_PAIRS_HISTORY).index.tolist()
)
pairs_hist["pair_grp"] = np.where(pairs_hist["pair"].isin(top_pairs), pairs_hist["pair"], "Others")
pair_order = top_pairs + (["Others"] if (pairs_hist["pair_grp"] == "Others").any() else [])

cp1, cp2 = st.columns(2)
with cp1:
    st.plotly_chart(
        stacked_over_time(pairs_hist, "pair_grp", "Volume by Pair Over Time",
                          normalized=False, order=pair_order),
        use_container_width=True,
    )
with cp2:
    st.plotly_chart(
        stacked_over_time(pairs_hist, "pair_grp", "Normalized Volume by Pair Over Time",
                          normalized=True, order=pair_order),
        use_container_width=True,
    )

c3, c4 = st.columns(2)
with c3:
    if not quote_agg.empty and quote_agg["volume"].sum() > 0:
        fig = px.pie(
            quote_agg, names="quote", values="volume", hole=0.5,
            color_discrete_sequence=PALETTE, title="24h Volume by Quote Currency",
        )
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(height=420, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
with c4:
    pc = f[(f["volume"] > 0) & f["price_chg"].notna()].sort_values("price_chg")
    if not pc.empty:
        pc = pc.assign(direction=np.where(pc["price_chg"] >= 0, "Up", "Down"))
        fig = px.bar(
            pc, x="price_chg", y="market", orientation="h", color="direction",
            color_discrete_map={"Up": GREEN, "Down": RED},
            text=pc["price_chg"].map(lambda v: f"{v:+.2f}%"),
            title="24h Price Change by Market (%)",
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=420, yaxis_title=None, xaxis_title="Change (%)", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
st.caption("Price changes are measured in each market's own quote currency (KRW, USDT, EUR, ...).")

hm = f.pivot_table(index="pair", columns="exchange", values="volume", aggfunc="sum", fill_value=0)
if not hm.empty:
    hm = hm.reindex(
        index=pair_agg["pair"].head(TOP_N).tolist(),
        columns=ex_agg["exchange"].head(TOP_N).tolist(),
    ).fillna(0)
    fig = px.imshow(
        np.log10(hm.replace(0, np.nan)), aspect="auto", color_continuous_scale="Viridis",
        labels=dict(color="log10(Volume)"),
        title="Heatmap — Pair × Exchange 24h Volume (log10)",
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Pair Summary")
st.dataframe(
    pair_agg.rename(columns={
        "pair": "Pair", "volume": "Volume 24h (USD)", "exchanges": "Exchanges", "share": "Share %",
    }),
    use_container_width=True, hide_index=True,
    column_config={
        "Volume 24h (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Share %": st.column_config.ProgressColumn(format="%.2f%%", min_value=0, max_value=100),
    },
)

st.divider()

# ═══════════════════════ Liquidity & Depth ═══════════════════════════
st.header("Liquidity & Order Book Depth")
default_band = 0.02 if 0.02 in bands else bands[len(bands) // 2]
band = st.select_slider(
    "Depth band (distance from price)",
    options=bands, value=default_band,
    format_func=lambda b: f"±{b * 100:g}%",
)
f["bid_depth"] = f["depth_bid"].apply(lambda a: depth_at(a, bands, band))
f["ask_depth"] = f["depth_ask"].apply(lambda a: depth_at(a, bands, band))

c1, c2 = st.columns(2)
with c1:
    dd = f[(f["bid_depth"].fillna(0) > 0) | (f["ask_depth"].fillna(0) > 0)].copy()
    if not dd.empty:
        dd["total"] = dd["bid_depth"].fillna(0) + dd["ask_depth"].fillna(0)
        order = dd.sort_values("total")["market"].tolist()
        m = dd.melt(id_vars="market", value_vars=["bid_depth", "ask_depth"],
                    var_name="side", value_name="usd")
        m["side"] = m["side"].map({"bid_depth": "Bids", "ask_depth": "Asks"})
        m["usd"] = m["usd"].fillna(0)
        fig = px.bar(
            m, x="usd", y="market", color="side", barmode="group", orientation="h",
            color_discrete_map={"Bids": GREEN, "Asks": RED},
            category_orders={"market": order},
            title=f"Order Book Depth within ±{band * 100:g}% (USD)",
        )
        fig.update_layout(height=480, yaxis_title=None, xaxis_title="Depth (USD)",
                          legend=dict(orientation="h", y=1.1, title=None))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No order-book depth is available for the selected band.")
with c2:
    sp = f[f["spread_pct"].notna()].sort_values("spread_pct", ascending=False)
    if not sp.empty:
        fig = px.bar(
            sp.sort_values("spread_pct"), x="spread_pct", y="market", orientation="h",
            color="exchange", color_discrete_sequence=PALETTE, log_x=True,
            text=sp.sort_values("spread_pct")["spread_pct"].map(lambda v: f"{v:.2f}%"),
            title="Bid-Ask Spread by Market (%, log scale)",
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(height=480, yaxis_title=None, xaxis_title="Spread (%)", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Depth = USD value of resting bids/asks within the selected distance from the market price. "
    "Markets with no 24h volume and very wide spreads are effectively inactive."
)

st.divider()

# ═══════════════════════════ All Markets ═════════════════════════════
st.header("All Markets")
bl = f"±{band * 100:g}%"
show = f.sort_values("volume", ascending=False).copy()
show["Price (quote)"] = show.apply(lambda r: fmt_quote_price(r["price"], r["quote"]), axis=1)
show["vol_status"] = show["vol_status"].fillna("no volume")
show = show.rename(columns={
    "exchange": "Exchange", "pair": "Pair", "venue": "Type", "price_chg": "Price Δ 24h %",
    "volume": "Volume 24h (USD)", "vol_chg": "Volume Δ 24h %", "share": "Share %",
    "vol_normalized": "Normalized Volume (USD)", "vol_status": "Volume Status",
    "spread_pct": "Spread %", "bid_depth": f"Bid Depth {bl} (USD)",
    "ask_depth": f"Ask Depth {bl} (USD)", "maker_fee": "Maker Fee %",
    "taker_fee": "Taker Fee %", "pair_url": "Trade",
})[[
    "Exchange", "Pair", "Type", "Price (quote)", "Price Δ 24h %", "Volume 24h (USD)",
    "Volume Δ 24h %", "Share %", "Normalized Volume (USD)", "Volume Status", "Spread %",
    f"Bid Depth {bl} (USD)", f"Ask Depth {bl} (USD)", "Maker Fee %", "Taker Fee %", "Trade",
]]
st.dataframe(
    show, use_container_width=True, hide_index=True,
    column_config={
        "Price Δ 24h %": st.column_config.NumberColumn(format="%+.2f%%"),
        "Volume 24h (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Volume Δ 24h %": st.column_config.NumberColumn(format="%+.2f%%"),
        "Share %": st.column_config.ProgressColumn(format="%.2f%%", min_value=0, max_value=100),
        "Normalized Volume (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Spread %": st.column_config.NumberColumn(format="%.2f%%"),
        f"Bid Depth {bl} (USD)": st.column_config.NumberColumn(format="$%.0f"),
        f"Ask Depth {bl} (USD)": st.column_config.NumberColumn(format="$%.0f"),
        "Maker Fee %": st.column_config.NumberColumn(format="%.2f%%"),
        "Taker Fee %": st.column_config.NumberColumn(format="%.2f%%"),
        "Trade": st.column_config.LinkColumn("Trade", display_text="Open ↗"),
    },
)
st.download_button(
    "⬇️ Download markets (CSV)", show.to_csv(index=False).encode("utf-8"),
    file_name="squid_quid_markets.csv", mime="text/csv",
)

# =========================================================
# METRIC GLOSSARY — RENDERED AT THE BOTTOM OF THE PAGE
# =========================================================
def render_metric_glossary():
    st.subheader("📖 Metric Glossary")
    st.markdown(
        """
        - **QUID Price (USD):** Aggregate spot price reported by the API, with its 24h change.
        - **Total Volume (24h):** Sum of the reported 24h trading volume (USD) across all QUID markets, compared with the previous 24h.
        - **Exchanges:** Number of unique exchanges listing QUID markets.
        - **Unique Pairs:** Number of distinct trading pairs across all exchanges.
        - **Top Exchange Share:** Share of total volume handled by the largest exchange.
        - **Top-3 Exchanges Share:** Combined share of total volume handled by the three largest exchanges.
        - **Over Time charts:** Built from daily snapshots of each market's 24h volume, recorded at the end of every UTC day.
        - **Normalized (stacked) charts:** 100% stacked bars showing each exchange's or pair's share of the day's total volume.
        - **Volume Change (24h):** Last 24h volume compared with the previous 24h, per exchange.
        - **Normalized Volume:** The API's adjusted volume figure; a large gap versus reported volume can indicate volume that is high relative to order-book liquidity.
        - **Bid-Ask Spread %:** (Best ask − best bid) ÷ mid price. Wider spreads mean higher trading cost and thinner liquidity.
        - **Order Book Depth:** USD value of resting bids and asks within a chosen distance (±%) of the market price.
        - **Price (quote):** Last price in the market's quote currency (KRW, USDT, EUR, ...), not converted to USD.
        """
    )


st.markdown("---")

render_metric_glossary()

st.markdown(
    """
    <div style="margin-top: 20px; margin-bottom: 20px; font-size: 16px;">
        <div style="display: flex; align-items: center; gap: 10px;">
            <img src="https://pbs.twimg.com/profile_images/2060406047391559681/sA9zPNKM_400x400.jpg" style="width:25px; height:25px; border-radius: 50%;">
            <span>Built by: <a href="https://x.com/0xeman_raz" target="_blank">Eman Raz</a></span>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)
st.markdown("")
st.markdown(
    """
    <!-- Support / Tips Box -->
    <div style="
        background-color: #F5F5F5;
        border-left: 5px solid #888;
        padding: 12px;
        border-radius: 10px;
        margin-top: 10px;
        font-size: 15px;
        color: #333;
    ">
        🎁 <b>Support / Tips:</b><br>
        <code>0x621bd661e3d57da1c8237209824827f1027abf62</code>
    </div>
    """,
    unsafe_allow_html=True
)
