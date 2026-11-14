"""
Squid (QUID) Markets Dashboard
Data source: https://defillama.com/api/public/markets/squid-3

Run:
    pip install streamlit pandas numpy requests plotly
    streamlit run squid_markets_dashboard.py

The script auto-detects the JSON schema (exchange / pair / price / volume / ...).
If a column is detected wrongly, fix it from the sidebar -> "Column mapping".
"""

import json

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ─────────────────────────────── Config ───────────────────────────────
API_URL = "https://defillama.com/api/public/markets/squid-3"

st.set_page_config(
    page_title="Squid (QUID) Markets Dashboard",
    page_icon="🦑",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem;}
    div[data-testid="stMetric"] {
        background: rgba(128,128,128,0.08);
        border: 1px solid rgba(128,128,128,0.25);
        border-radius: 12px;
        padding: 14px 18px;
    }
    div[data-testid="stMetricLabel"] p {font-size: 0.85rem; opacity: 0.75;}
    </style>
    """,
    unsafe_allow_html=True,
)

PALETTE = px.colors.qualitative.Set2


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


def find_records(obj):
    """Return the largest list of dicts found anywhere in the JSON."""
    best = []

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o) and len(o) > len(best):
                best = o
            for x in o[:200]:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(obj)
    return best


def top_level_meta(obj):
    if not isinstance(obj, dict):
        return {}
    return {
        k: v for k, v in obj.items()
        if isinstance(v, (str, int, float, bool)) and v not in ("", None)
    }


def pick(cols, patterns, exclude=()):
    """Pick the best matching column: exact / suffix match first, then substring."""
    low = {c: c.lower() for c in cols}
    for p in patterns:
        for c, l in low.items():
            if l == p or l.endswith("." + p):
                return c
    for p in patterns:
        for c, l in low.items():
            if p in l and not any(e in l for e in exclude):
                return c
    return None


def to_num(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    cleaned = s.astype(str).str.replace(r"[,$%\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def fmt_usd(n, decimals=2):
    if n is None or pd.isna(n):
        return "—"
    a = abs(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"${n / div:,.{decimals}f}{suf}"
    return f"${n:,.{decimals}f}"


def fmt_price(n):
    if n is None or pd.isna(n):
        return "—"
    if abs(n) >= 1:
        return f"${n:,.4f}"
    return f"${n:,.8f}"


def top_n_with_others(s: pd.Series, n=10):
    s = s.sort_values(ascending=False)
    if len(s) <= n:
        return s
    top = s.iloc[:n].copy()
    top["Others"] = s.iloc[n:].sum()
    return top


def vwap(group: pd.DataFrame):
    g = group.dropna(subset=["price"])
    w = g["volume"]
    if len(g) == 0:
        return np.nan
    if w.sum() <= 0:
        return g["price"].mean()
    return np.average(g["price"], weights=w)


# ─────────────────────────────── Sidebar ──────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")
    url = st.text_input("API URL", API_URL)
    uploaded = st.file_uploader(
        "Or upload a JSON file", type="json",
        help="Use this if the API blocks requests from your machine/server.",
    )
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ─────────────────────────────── Load data ────────────────────────────
if uploaded is not None:
    data = json.load(uploaded)
else:
    try:
        data = fetch(url)
    except Exception as e:
        st.error(f"Could not fetch the API: {e}")
        st.info("Open the URL in your browser, save the JSON, and upload it from the sidebar.")
        st.stop()

records = find_records(data)
if not records:
    st.error("No list of records was found in the JSON response.")
    st.json(data)
    st.stop()

raw = pd.json_normalize(records, sep=".")
for c in raw.columns:
    if raw[c].map(lambda x: isinstance(x, (list, dict))).any():
        raw[c] = raw[c].astype(str)

cols = list(raw.columns)

# ─────────────────────────── Column detection ─────────────────────────
auto = {
    "exchange": pick(cols, ["exchange", "venue", "market_name", "source", "platform", "name"]),
    "pair": pick(cols, ["pair", "market", "symbol", "ticker"], exclude=("exchange",)),
    "price": pick(cols, ["price", "last", "price_usd", "converted_last.usd"]),
    "volume": pick(cols, ["volume_usd", "usd_volume", "converted_volume.usd",
                          "volume24h", "volume_24h", "volume", "vol"]),
    "type": pick(cols, ["type", "category", "is_dex", "dex", "cex"]),
    "updated": pick(cols, ["last_traded_at", "last_updated", "updated", "timestamp", "last_traded", "time", "date"]),
}

with st.sidebar:
    with st.expander("🧭 Column mapping (auto-detected)"):
        mapping = {}
        for key, default in auto.items():
            options = ["— none —"] + cols
            idx = options.index(default) if default in options else 0
            choice = st.selectbox(key, options, index=idx, key=f"map_{key}")
            mapping[key] = None if choice == "— none —" else choice

# ─────────────────────────── Build clean frame ────────────────────────
df = pd.DataFrame(index=raw.index)
df["exchange"] = raw[mapping["exchange"]].astype(str) if mapping["exchange"] else "Unknown"

if mapping["pair"]:
    df["pair"] = raw[mapping["pair"]].astype(str)
elif {"base", "target"} <= set(c.lower() for c in cols):
    b = next(c for c in cols if c.lower() == "base")
    t = next(c for c in cols if c.lower() == "target")
    df["pair"] = raw[b].astype(str) + "/" + raw[t].astype(str)
else:
    df["pair"] = "N/A"

df["price"] = to_num(raw[mapping["price"]]) if mapping["price"] else np.nan
df["volume"] = to_num(raw[mapping["volume"]]).fillna(0) if mapping["volume"] else 0.0
df["type"] = raw[mapping["type"]].astype(str) if mapping["type"] else None

if mapping["updated"]:
    u = raw[mapping["updated"]]
    num = pd.to_numeric(u, errors="coerce")
    if num.notna().mean() > 0.8:
        unit = "ms" if num.median() > 1e11 else "s"
        df["updated"] = pd.to_datetime(num, unit=unit, utc=True, errors="coerce")
    else:
        df["updated"] = pd.to_datetime(u, utc=True, errors="coerce")
    df["age_h"] = (pd.Timestamp.now(tz="UTC") - df["updated"]).dt.total_seconds() / 3600
else:
    df["updated"] = pd.NaT
    df["age_h"] = np.nan

has_price = df["price"].notna().any()
has_volume = (df["volume"] > 0).any()
has_type = df["type"].notna().any()
has_time = df["updated"].notna().any()

# ─────────────────────────────── Filters ──────────────────────────────
with st.sidebar:
    st.subheader("🔎 Filters")
    all_ex = sorted(df["exchange"].unique())
    sel_ex = st.multiselect("Exchanges", all_ex, default=all_ex)
    min_vol = st.number_input("Min volume per market ($)", min_value=0.0, value=0.0, step=100.0)
    if has_type:
        all_types = sorted(df["type"].dropna().unique())
        sel_types = st.multiselect("Type", all_types, default=all_types)
    else:
        sel_types = None
    max_dev = 0.0
    if has_price:
        max_dev = st.number_input(
            "Exclude price outliers (± % from median, 0 = off)",
            min_value=0.0, value=0.0, step=5.0,
        )
    top_n = st.slider("Top N in charts", 5, 30, 15)

f = df[df["exchange"].isin(sel_ex) & (df["volume"] >= min_vol)].copy()
if has_type and sel_types is not None:
    f = f[f["type"].isin(sel_types)]
if has_price and max_dev > 0:
    med = f["price"].median()
    if pd.notna(med) and med > 0:
        f = f[(f["price"] / med - 1).abs() * 100 <= max_dev]

if f.empty:
    st.warning("No data left after applying the filters.")
    st.stop()

# ─────────────────────────────── Aggregates ───────────────────────────
total_vol = f["volume"].sum()
f["share"] = f["volume"] / total_vol * 100 if total_vol > 0 else 0.0

ex_agg = (
    f.groupby("exchange")
    .agg(volume=("volume", "sum"), markets=("pair", "count"), pairs=("pair", "nunique"))
    .reset_index()
)
ex_agg["vwap"] = ex_agg["exchange"].map(
    {k: vwap(g) for k, g in f.groupby("exchange")}
) if has_price else np.nan
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

market_vwap = vwap(f) if has_price else np.nan
if has_price:
    f["dev_pct"] = (f["price"] / market_vwap - 1) * 100 if market_vwap else np.nan

top1 = ex_agg["share"].iloc[0] if len(ex_agg) else 0
top3 = ex_agg["share"].head(3).sum()
hhi = float(((ex_agg["share"] / 100) ** 2).sum() * 10000) if total_vol > 0 else np.nan

# ─────────────────────────────── Header ───────────────────────────────
st.title("🦑 Squid (QUID) — Markets Analytics Dashboard")
meta = top_level_meta(data)
caption = f"Source: DefiLlama Markets API · {len(df):,} markets loaded"
if meta:
    caption += " · " + " · ".join(f"{k}: {v}" for k, v in list(meta.items())[:4])
st.caption(caption)

# ─────────────────────────────── KPIs ─────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Volume", fmt_usd(total_vol))
k2.metric("Markets", f"{len(f):,}")
k3.metric("Exchanges", f"{f['exchange'].nunique():,}")
k4.metric("Unique Pairs", f"{f['pair'].nunique():,}")
k5.metric("VWAP Price", fmt_price(market_vwap) if has_price else "—")

k6, k7, k8, k9, k10 = st.columns(5)
if has_price:
    pmin, pmax = f["price"].min(), f["price"].max()
    k6.metric("Price Range (Min–Max)", f"{fmt_price(pmin)} – {fmt_price(pmax)}")
    spread = (pmax / pmin - 1) * 100 if pmin and pmin > 0 else np.nan
    k7.metric("Cross-market Spread", f"{spread:,.2f}%" if pd.notna(spread) else "—")
else:
    k6.metric("Price Range", "—")
    k7.metric("Cross-market Spread", "—")
k8.metric("Top Exchange Share", f"{top1:.1f}%", help=ex_agg["exchange"].iloc[0])
k9.metric("Top-3 Exchanges Share", f"{top3:.1f}%")
k10.metric(
    "HHI (Concentration)", f"{hhi:,.0f}" if pd.notna(hhi) else "—",
    help="Herfindahl-Hirschman Index on exchange volume share. "
         "<1500 = competitive, 1500–2500 = moderate, >2500 = highly concentrated.",
)

st.divider()

# ─────────────────────────────── Tabs ─────────────────────────────────
tab_over, tab_ex, tab_pair, tab_price, tab_table, tab_raw = st.tabs(
    ["📊 Overview", "🏦 Exchanges", "🔗 Pairs", "💲 Price Analysis", "📋 Tables", "🧾 Raw Data"]
)

# ───────── Overview ─────────
with tab_over:
    if not has_volume:
        st.warning("No volume column detected — check the column mapping in the sidebar.")
    c1, c2 = st.columns([3, 2])

    with c1:
        d = ex_agg.head(top_n).sort_values("volume")
        fig = px.bar(
            d, x="volume", y="exchange", orientation="h",
            text=d["volume"].map(fmt_usd), color="volume",
            color_continuous_scale="Viridis",
            title=f"Top {min(top_n, len(ex_agg))} Exchanges by Volume",
        )
        fig.update_layout(coloraxis_showscale=False, yaxis_title=None, xaxis_title="Volume (USD)", height=480)
        fig.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        s = top_n_with_others(ex_agg.set_index("exchange")["volume"], 10)
        fig = px.pie(
            names=s.index, values=s.values, hole=0.55,
            color_discrete_sequence=PALETTE, title="Volume Share by Exchange",
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
        fig.update_layout(height=480, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        tm = f[f["volume"] > 0]
        if not tm.empty:
            fig = px.treemap(
                tm, path=["exchange", "pair"], values="volume",
                color="volume", color_continuous_scale="Tealgrn",
                title="Volume Treemap (Exchange → Pair)",
            )
            fig.update_layout(height=450, coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)
    with c4:
        if has_type:
            t = f.groupby("type")["volume"].sum().reset_index()
            fig = px.pie(
                t, names="type", values="volume", hole=0.5,
                color_discrete_sequence=PALETTE, title="Volume by Market Type",
            )
            fig.update_layout(height=450)
            st.plotly_chart(fig, use_container_width=True)
        else:
            pareto = f.sort_values("volume", ascending=False).reset_index(drop=True)
            pareto["cum"] = pareto["volume"].cumsum() / total_vol * 100 if total_vol > 0 else 0
            fig = go.Figure()
            fig.add_bar(x=pareto.index + 1, y=pareto["volume"], name="Volume", marker_color="#4c78a8")
            fig.add_scatter(x=pareto.index + 1, y=pareto["cum"], name="Cumulative %",
                            yaxis="y2", line=dict(color="#f58518", width=3))
            fig.update_layout(
                title="Pareto — Market Volume Concentration",
                xaxis_title="Market rank", yaxis_title="Volume (USD)",
                yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 105]),
                height=450, legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig, use_container_width=True)

# ───────── Exchanges ─────────
with tab_ex:
    c1, c2 = st.columns(2)
    with c1:
        d = ex_agg.head(top_n)
        fig = px.bar(
            d, x="exchange", y="markets", color="pairs",
            color_continuous_scale="Blues",
            title="Number of Markets per Exchange (color = unique pairs)",
        )
        fig.update_layout(xaxis_title=None, height=420)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.scatter(
            ex_agg, x="markets", y="volume", size="volume", color="share",
            hover_name="exchange", color_continuous_scale="Turbo", size_max=55,
            log_y=bool((ex_agg["volume"] > 0).all()),
            title="Liquidity Map — Markets vs Volume (bubble = volume)",
        )
        fig.update_layout(height=420, yaxis_title="Volume (USD, log)")
        st.plotly_chart(fig, use_container_width=True)

    ex_sorted = ex_agg.sort_values("volume", ascending=False)
    ex_sorted["cum_share"] = ex_sorted["share"].cumsum()
    fig = go.Figure()
    fig.add_bar(x=ex_sorted["exchange"].head(top_n), y=ex_sorted["share"].head(top_n),
                name="Share %", marker_color="#54a24b")
    fig.add_scatter(x=ex_sorted["exchange"].head(top_n), y=ex_sorted["cum_share"].head(top_n),
                    name="Cumulative %", line=dict(color="#e45756", width=3), mode="lines+markers")
    fig.update_layout(title="Exchange Volume Share & Cumulative Concentration",
                      yaxis_title="%", height=420, legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, use_container_width=True)

# ───────── Pairs ─────────
with tab_pair:
    c1, c2 = st.columns(2)
    with c1:
        d = pair_agg.head(top_n).sort_values("volume")
        fig = px.bar(
            d, x="volume", y="pair", orientation="h", color="exchanges",
            color_continuous_scale="Sunset", text=d["volume"].map(fmt_usd),
            title=f"Top {min(top_n, len(pair_agg))} Pairs by Volume (color = # exchanges)",
        )
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(yaxis_title=None, xaxis_title="Volume (USD)", height=520)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        s = top_n_with_others(pair_agg.set_index("pair")["volume"], 10)
        fig = px.pie(names=s.index, values=s.values, hole=0.5,
                     color_discrete_sequence=px.colors.qualitative.Pastel,
                     title="Volume Share by Pair")
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(height=520, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    hm = f.pivot_table(index="pair", columns="exchange", values="volume", aggfunc="sum", fill_value=0)
    if not hm.empty:
        top_pairs = pair_agg["pair"].head(top_n).tolist()
        top_ex = ex_agg["exchange"].head(top_n).tolist()
        hm = hm.reindex(index=top_pairs, columns=top_ex).fillna(0)
        fig = px.imshow(
            np.log10(hm.replace(0, np.nan)), aspect="auto", color_continuous_scale="Viridis",
            labels=dict(color="log10(Volume)"),
            title="Heatmap — Pair × Exchange Volume (log10)",
        )
        fig.update_layout(height=520)
        st.plotly_chart(fig, use_container_width=True)

# ───────── Price analysis ─────────
with tab_price:
    if not has_price:
        st.info("No price column detected. Check the column mapping in the sidebar.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            fig = px.scatter(
                f, x="volume", y="price", color="exchange", hover_data=["pair"],
                log_x=bool((f["volume"] > 0).any()), opacity=0.8,
                title="Price vs Volume per Market",
            )
            fig.add_hline(y=market_vwap, line_dash="dash", line_color="gray",
                          annotation_text="VWAP", annotation_position="top left")
            fig.update_layout(height=450, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            fig = px.histogram(
                f, x="price", nbins=40, color_discrete_sequence=["#4c78a8"],
                title="Price Distribution across Markets",
            )
            fig.add_vline(x=market_vwap, line_dash="dash", line_color="red", annotation_text="VWAP")
            fig.update_layout(height=450)
            st.plotly_chart(fig, use_container_width=True)

        dd = f.sort_values("volume", ascending=False).head(top_n * 2).copy()
        dd["label"] = dd["exchange"] + " · " + dd["pair"]
        dd["dir"] = np.where(dd["dev_pct"] >= 0, "Above VWAP", "Below VWAP")
        fig = px.bar(
            dd.sort_values("dev_pct"), x="dev_pct", y="label", orientation="h", color="dir",
            color_discrete_map={"Above VWAP": "#54a24b", "Below VWAP": "#e45756"},
            title="Price Deviation from VWAP — largest markets (%)",
        )
        fig.update_layout(height=max(450, 22 * len(dd)), yaxis_title=None, xaxis_title="Deviation (%)")
        st.plotly_chart(fig, use_container_width=True)

        c3, c4 = st.columns(2)
        with c3:
            ex_p = ex_agg.dropna(subset=["vwap"]).head(top_n)
            fig = px.bar(ex_p, x="exchange", y="vwap", color="vwap",
                         color_continuous_scale="Cividis",
                         title="Volume-weighted Price per Exchange")
            fig.update_layout(height=420, coloraxis_showscale=False, xaxis_title=None)
            st.plotly_chart(fig, use_container_width=True)
        with c4:
            if has_time:
                fig = px.histogram(
                    f.dropna(subset=["age_h"]), x="age_h", nbins=40,
                    color_discrete_sequence=["#b279a2"],
                    title="Data Freshness — hours since last update",
                )
                fig.update_layout(height=420, xaxis_title="Hours ago")
                st.plotly_chart(fig, use_container_width=True)
            else:
                fig = px.box(f, x="exchange", y="price", title="Price Dispersion per Exchange")
                fig.update_layout(height=420, xaxis_title=None)
                st.plotly_chart(fig, use_container_width=True)

# ───────── Tables ─────────
with tab_table:
    st.subheader("Exchange Summary")
    st.dataframe(
        ex_agg.rename(columns={
            "exchange": "Exchange", "volume": "Volume (USD)", "markets": "Markets",
            "pairs": "Unique Pairs", "vwap": "VWAP Price", "share": "Share %",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Volume (USD)": st.column_config.NumberColumn(format="$%,.0f"),
            "VWAP Price": st.column_config.NumberColumn(format="$%.6f"),
            "Share %": st.column_config.ProgressColumn(
                format="%.2f%%", min_value=0, max_value=100),
        },
    )

    st.subheader("Pair Summary")
    st.dataframe(
        pair_agg.rename(columns={
            "pair": "Pair", "volume": "Volume (USD)", "exchanges": "Exchanges", "share": "Share %",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Volume (USD)": st.column_config.NumberColumn(format="$%,.0f"),
            "Share %": st.column_config.ProgressColumn(
                format="%.2f%%", min_value=0, max_value=100),
        },
    )

    st.subheader("All Markets")
    show = f.sort_values("volume", ascending=False).drop(columns=["age_h"], errors="ignore")
    if not has_type:
        show = show.drop(columns=["type"], errors="ignore")
    if not has_time:
        show = show.drop(columns=["updated"], errors="ignore")
    if not has_price:
        show = show.drop(columns=["price", "dev_pct"], errors="ignore")
    cfg = {
        "volume": st.column_config.NumberColumn("Volume (USD)", format="$%,.0f"),
        "price": st.column_config.NumberColumn("Price", format="$%.6f"),
        "share": st.column_config.ProgressColumn("Share %", format="%.2f%%", min_value=0, max_value=100),
        "dev_pct": st.column_config.NumberColumn("Dev. vs VWAP %", format="%.2f%%"),
    }
    st.dataframe(show, use_container_width=True, hide_index=True, column_config=cfg)
    st.download_button(
        "⬇️ Download markets (CSV)", show.to_csv(index=False).encode("utf-8"),
        file_name="squid_quid_markets.csv", mime="text/csv",
    )

# ───────── Raw ─────────
with tab_raw:
    st.caption("Data exactly as returned by the API (flattened).")
    st.dataframe(raw, use_container_width=True)
    with st.expander("Raw JSON"):
        st.json(data if not isinstance(data, list) else data[:50])

st.divider()
st.caption("Built with Streamlit & Plotly · Data: DefiLlama")
