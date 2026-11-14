"""
Squid (QUID) Markets Dashboard
Data source: https://defillama.com/api/public/markets/squid-3

Run:
    pip install streamlit pandas numpy requests plotly
    streamlit run squid_markets_dashboard.py
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
LOGO_URL = "https://assets.coingecko.com/coins/images/102174404/standard/squid.jpg?1783587866"
TOP_N = 15

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


def find_key(obj, key):
    """Find the first scalar value stored under `key` anywhere in the JSON."""
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], (str, int, float)):
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj[:50]:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


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


def top_n_with_others(s: pd.Series, n=10):
    s = s.sort_values(ascending=False)
    if len(s) <= n:
        return s
    top = s.iloc[:n].copy()
    top["Others"] = s.iloc[n:].sum()
    return top


def vwap(group: pd.DataFrame):
    g = group.dropna(subset=["price"])
    if len(g) == 0:
        return np.nan
    w = g["volume"]
    if w.sum() <= 0:
        return g["price"].mean()
    return np.average(g["price"], weights=w)


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
mapping = {
    "exchange": pick(cols, ["exchange", "venue", "market_name", "source", "platform", "name"]),
    "pair": pick(cols, ["pair", "market", "symbol", "ticker"], exclude=("exchange",)),
    "price": pick(cols, ["price", "last", "price_usd", "converted_last.usd"]),
    "volume": pick(cols, ["volume_usd", "usd_volume", "converted_volume.usd",
                          "volume24h", "volume_24h", "volume", "vol"]),
    "type": pick(cols, ["type", "category", "is_dex", "dex", "cex"]),
    "updated": pick(cols, ["last_traded_at", "last_updated", "updated", "timestamp",
                           "last_traded", "time", "date"]),
}

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

if mapping["type"]:
    df["type"] = raw[mapping["type"]].astype(str)

if mapping["updated"]:
    u = raw[mapping["updated"]]
    num = pd.to_numeric(u, errors="coerce")
    if num.notna().mean() > 0.8:
        unit = "ms" if num.median() > 1e11 else "s"
        df["updated"] = pd.to_datetime(num, unit=unit, utc=True, errors="coerce")
    else:
        df["updated"] = pd.to_datetime(u, utc=True, errors="coerce")

has_price = df["price"].notna().any()
f = df.copy()

# ─────────────────────────────── Aggregates ───────────────────────────
total_vol = f["volume"].sum()
f["share"] = f["volume"] / total_vol * 100 if total_vol > 0 else 0.0

ex_agg = (
    f.groupby("exchange")
    .agg(volume=("volume", "sum"), markets=("pair", "count"), pairs=("pair", "nunique"))
    .reset_index()
)
ex_agg["vwap"] = (
    ex_agg["exchange"].map({k: vwap(g) for k, g in f.groupby("exchange")})
    if has_price else np.nan
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

if has_price:
    mv = vwap(f)
    f["dev_pct"] = (f["price"] / mv - 1) * 100 if mv else np.nan

top1 = ex_agg["share"].iloc[0] if len(ex_agg) else 0
top3 = ex_agg["share"].head(3).sum()

# ─────────────────────────────── Header ───────────────────────────────
logo_col, title_col = st.columns([1, 14], vertical_alignment="center")
with logo_col:
    st.image(LOGO_URL, width=64)
with title_col:
    st.title("Squid (QUID) — Markets Analytics Dashboard")

last_updated = find_key(data, "last_updated")
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
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total Volume", fmt_usd(total_vol))
k2.metric("Exchanges", f"{f['exchange'].nunique():,}")
k3.metric("Unique Pairs", f"{f['pair'].nunique():,}")
k4.metric("Top Exchange Share", f"{top1:.1f}%", help=ex_agg["exchange"].iloc[0])
k5.metric("Top-3 Exchanges Share", f"{top3:.1f}%")

st.divider()

# ════════════════════════════ Overview ═══════════════════════════════
st.header("Overview")
c1, c2 = st.columns([3, 2])

with c1:
    d = ex_agg.head(TOP_N).sort_values("volume")
    fig = px.bar(
        d, x="volume", y="exchange", orientation="h",
        text=d["volume"].map(fmt_usd), color="volume",
        color_continuous_scale="Viridis",
        title=f"Top {min(TOP_N, len(ex_agg))} Exchanges by Volume",
    )
    fig.update_layout(coloraxis_showscale=False, yaxis_title=None,
                      xaxis_title="Volume (USD)", height=480)
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

tm = f[f["volume"] > 0]
if not tm.empty:
    fig = px.treemap(
        tm, path=["exchange", "pair"], values="volume",
        color="volume", color_continuous_scale="Tealgrn",
        title="Volume Treemap (Exchange → Pair)",
    )
    fig.update_layout(height=450, coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ════════════════════════════ Exchanges ══════════════════════════════
st.header("Exchanges")
c1, c2 = st.columns(2)
with c1:
    d = ex_agg.head(TOP_N)
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
        "Share %": st.column_config.ProgressColumn(format="%.2f%%", min_value=0, max_value=100),
    },
)

st.divider()

# ═════════════════════════════ Pairs ═════════════════════════════════
st.header("Pairs")
c1, c2 = st.columns(2)
with c1:
    d = pair_agg.head(TOP_N).sort_values("volume")
    fig = px.bar(
        d, x="volume", y="pair", orientation="h", color="exchanges",
        color_continuous_scale="Sunset", text=d["volume"].map(fmt_usd),
        title=f"Top {min(TOP_N, len(pair_agg))} Pairs by Volume (color = # exchanges)",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(yaxis_title=None, xaxis_title="Volume (USD)", height=520)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    s = top_n_with_others(pair_agg.set_index("pair")["volume"], 10)
    fig = px.pie(
        names=s.index, values=s.values, hole=0.5,
        color_discrete_sequence=px.colors.qualitative.Pastel,
        title="Volume Share by Pair",
    )
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(height=520, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

hm = f.pivot_table(index="pair", columns="exchange", values="volume", aggfunc="sum", fill_value=0)
if not hm.empty:
    hm = hm.reindex(
        index=pair_agg["pair"].head(TOP_N).tolist(),
        columns=ex_agg["exchange"].head(TOP_N).tolist(),
    ).fillna(0)
    fig = px.imshow(
        np.log10(hm.replace(0, np.nan)), aspect="auto", color_continuous_scale="Viridis",
        labels=dict(color="log10(Volume)"),
        title="Heatmap — Pair × Exchange Volume (log10)",
    )
    fig.update_layout(height=520)
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Pair Summary")
st.dataframe(
    pair_agg.rename(columns={
        "pair": "Pair", "volume": "Volume (USD)", "exchanges": "Exchanges", "share": "Share %",
    }),
    use_container_width=True, hide_index=True,
    column_config={
        "Volume (USD)": st.column_config.NumberColumn(format="$%,.0f"),
        "Share %": st.column_config.ProgressColumn(format="%.2f%%", min_value=0, max_value=100),
    },
)

st.divider()

# ═══════════════════════════ All Markets ═════════════════════════════
st.header("All Markets")
show = f.sort_values("volume", ascending=False)
if not has_price:
    show = show.drop(columns=["price", "dev_pct"], errors="ignore")
st.dataframe(
    show, use_container_width=True, hide_index=True,
    column_config={
        "volume": st.column_config.NumberColumn("Volume (USD)", format="$%,.0f"),
        "price": st.column_config.NumberColumn("Price", format="$%.6f"),
        "share": st.column_config.ProgressColumn("Share %", format="%.2f%%", min_value=0, max_value=100),
        "dev_pct": st.column_config.NumberColumn("Dev. vs VWAP %", format="%.2f%%"),
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
        - **Total Volume:** Sum of the trading volume (USD) across all listed QUID markets.
        - **Exchanges:** Number of unique exchanges listing QUID markets.
        - **Unique Pairs:** Number of distinct trading pairs across all exchanges.
        - **Top Exchange Share:** Share of total volume handled by the largest exchange.
        - **Top-3 Exchanges Share:** Combined share of total volume handled by the three largest exchanges.
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
