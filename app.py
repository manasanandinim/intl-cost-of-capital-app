"""
International Cost of Capital
-----------------------------
Streamlit app that compares a project's cost of equity, cost of debt and WACC
in U.S. dollars and in the currency of a selected country.

Data (fetched live, cached for 6 hours, with a "Refresh data" button):
  * Industry unlevered betas ........ Aswath Damodaran, betas.xls
  * BBB (Baa2) USD default spread .... Aswath Damodaran, ratings.html (large non-financial firms)
  * Corporate tax rates .............. OECD Corporate Tax Statistics (combined statutory CIT rate)
  * 10-year government bond yields ... Trading Economics

The math lives in cost_of_capital.py.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import cost_of_capital as coc
from cost_of_capital import COUNTRIES, Inputs, compute

st.set_page_config(page_title="International Cost of Capital", page_icon="🌐", layout="wide")

USD_COLOR = "#2a78d6"   # categorical slot 1 (blue)
FC_COLOR = "#eb6834"    # categorical slot 2 (orange)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; max-width: 1250px;}
      .src-card {border: 1px solid rgba(128,128,128,.25); border-radius: 10px;
                 padding: .7rem .9rem; height: 100%;}
      .src-card .lbl {font-size: .78rem; opacity: .7; text-transform: uppercase; letter-spacing: .03em;}
      .src-card .val {font-size: 1.25rem; font-weight: 600; margin: .1rem 0;}
      .src-card .meta {font-size: .78rem; opacity: .75;}
      .pill {display:inline-block; font-size:.7rem; padding:.05rem .45rem; border-radius:999px;
             margin-left:.3rem; vertical-align: middle; border: 1px solid;}
      .pill.live {color:#0b7a3e; border-color:#0b7a3e;}
      .pill.snap {color:#9a6200; border-color:#9a6200;}
      .pill.man {color:#6250d6; border-color:#6250d6;}
      .cmp th, .cmp td {padding: .45rem .7rem;}
      .cmp {width:100%; border-collapse: collapse; font-size: .95rem;}
      .cmp tr {border-bottom: 1px solid rgba(128,128,128,.2);}
      .cmp td.num {text-align:right; font-variant-numeric: tabular-nums;}
      .cmp tr.total td {font-weight:700; font-size:1.05rem;}
      .cmp th {text-align:right; font-weight:600;}
      .cmp th:first-child {text-align:left;}
    </style>
    """,
    unsafe_allow_html=True,
)


def pct(x: float | None, d: int = 2) -> str:
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


# ---------------------------------------------------------------------------
# Cached data access
# ---------------------------------------------------------------------------
CACHE_TTL = 6 * 3600


@st.cache_data(ttl=CACHE_TTL, show_spinner="Downloading Damodaran industry betas...")
def get_betas():
    return coc.fetch_betas()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Downloading Damodaran BBB spread...")
def get_bbb():
    return coc.fetch_bbb_spread()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Downloading OECD corporate tax rates...")
def get_tax():
    return coc.fetch_tax_rates()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Downloading Trading Economics bond yields...")
def get_bond_table():
    return coc.fetch_bond_table()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Downloading 10-year yield...")
def get_yield(country: str):
    return coc.fetch_country_yield(country, get_bond_table())


# ---------------------------------------------------------------------------
# Sidebar: settings
# ---------------------------------------------------------------------------
betas_src = get_betas()
bbb_src = get_bbb()
tax_src = get_tax()
betas_df: pd.DataFrame = betas_src["data"]

industries = betas_df["industry"].tolist()
countries = list(COUNTRIES.keys())

with st.sidebar:
    st.header("Project settings")
    industry = st.selectbox(
        "Industry", industries,
        index=industries.index(coc.DEFAULT_INDUSTRY) if coc.DEFAULT_INDUSTRY in industries else 0,
    )
    country = st.selectbox("Selected country", countries,
                           index=countries.index(coc.DEFAULT_COUNTRY))
    currency = COUNTRIES[country][2]

    # Fetch the two yields now that the country is known
    us_y_src = get_yield("United States")
    fc_y_src = get_yield(country)

    tax_us_fetched, tax_us_year = coc.tax_rate_for("United States", tax_src["data"])
    tax_fc_fetched, tax_fc_year = coc.tax_rate_for(country, tax_src["data"])

    tax_choice = st.radio(
        "Project tax rate", ["U.S.", country if country != "United States" else "Selected country"],
        horizontal=True,
        help="One tax rate is used everywhere: to relever the beta and for the debt tax shield, "
             "in both currencies.",
    )
    use_fc_tax = tax_choice != "U.S."

    erp = st.number_input("Equity risk premium (%)", min_value=0.0, max_value=20.0,
                          value=coc.DEFAULT_ERP * 100, step=0.1, format="%.2f") / 100
    debt_share = st.slider("Target debt share, D/(D+E) (%)", 0, 90,
                           int(coc.DEFAULT_DEBT_SHARE * 100), 1) / 100

    st.divider()
    st.subheader("Override fetched data")
    st.caption("Tick a box to replace the downloaded value with your own.")

    beta_fetched = float(betas_df.loc[betas_df["industry"] == industry, "unlevered_beta"].iloc[0])

    def override(label: str, fetched: float | None, key: str, is_pct: bool = True,
                 force: bool = False, default: float | None = None) -> tuple[float | None, bool]:
        """A checkbox + number input. Returns (value in decimals, overridden?)."""
        shown = fetched if fetched is not None else default
        if force:
            st.markdown(f"**{label}** (required: no fetched value)")
            on = True
        else:
            on = st.checkbox(label, key=f"chk_{key}")
        if on:
            v = st.number_input(
                f"{label} {'(%)' if is_pct else ''}".strip(),
                value=float(round((shown or 0) * (100 if is_pct else 1), 4)),
                step=0.01, format="%.4f", key=f"val_{key}", label_visibility="collapsed")
            return (v / 100 if is_pct else v), True
        return fetched, False

    beta_u, beta_man = override("Unlevered beta", beta_fetched, f"beta_{industry}", is_pct=False)
    tax_us, tax_us_man = override("U.S. corporate tax rate", tax_us_fetched, "tax_us")
    fc_tax_missing = tax_fc_fetched is None
    if country != "United States":
        tax_fc, tax_fc_man = override(
            f"{country} corporate tax rate", tax_fc_fetched, f"tax_fc_{country}",
            force=fc_tax_missing and use_fc_tax, default=tax_us)
        if fc_tax_missing:
            st.caption(f"OECD has no rate for {country}. Enter one above to use the {country} rate.")
    else:
        tax_fc, tax_fc_man = tax_us, tax_us_man
    spread_usd, spread_man = override("USD BBB spread", bbb_src["data"], "spread_usd")
    rf_us, rf_us_man = override("U.S. 10-year yield", us_y_src["data"], "rf_us")
    if country != "United States":
        rf_fc, rf_fc_man = override(f"{country} 10-year yield", fc_y_src["data"], f"rf_fc_{country}")
    else:
        rf_fc, rf_fc_man = rf_us, rf_us_man

    with st.expander("Advanced: currency conversion method"):
        method_label = st.radio(
            "Convert USD rates into the foreign currency with",
            ["Multiplicative (exact)", "Additive (approximation)"],
            help="Multiplicative: (1+r_FC) = (1+r_USD)(1+rf_FC)/(1+rf_US). "
                 "Additive: r_FC = r_USD + (rf_FC - rf_US).",
        )
    method = "additive" if method_label.startswith("Additive") else "multiplicative"

    if use_fc_tax and tax_fc is None:
        tax_fc = tax_us
    tax_rate = tax_fc if use_fc_tax else tax_us
    tax_label = f"{country if use_fc_tax else 'U.S.'} rate"

    # Foreign-currency BBB spread: auto unless switched on
    base = Inputs(unlevered_beta=beta_u, tax_rate=tax_rate, erp=erp, debt_share=debt_share,
                  rf_usd=rf_us, rf_fc=rf_fc, spread_usd=spread_usd, method=method)
    auto_spread_fc = compute(base).spread_fc_auto

    st.divider()
    st.subheader(f"{currency} BBB spread")
    fc_override_on = st.toggle(f"Override the {currency} spread", key=f"fcs_on_{country}")
    if fc_override_on:
        spread_fc_in = st.number_input(
            f"{currency} BBB spread (%)", value=float(round(auto_spread_fc * 100, 4)),
            step=0.01, format="%.4f", key=f"fcs_val_{country}") / 100
    else:
        spread_fc_in = None
        st.caption(f"Auto-calculated: **{pct(auto_spread_fc, 3)}** "
                   "(the USD cost of debt converted into this currency, minus its risk-free rate).")

    st.divider()
    if st.button("🔄 Refresh data", width="stretch",
                 help="Clear the cache and download every source again."):
        st.cache_data.clear()
        st.rerun()

inputs = Inputs(**{**base.__dict__, "spread_fc_override": spread_fc_in})
res = compute(inputs)
same_ccy = country == "United States"
fc_name = "USD (selected country is the U.S.)" if same_ccy else f"{currency} ({country})"

# ---------------------------------------------------------------------------
# Header + data status
# ---------------------------------------------------------------------------
st.title("International cost of capital")
st.markdown(
    f"Cost of equity, cost of debt and WACC for a **{industry}** project, in **U.S. dollars** "
    f"and in **{currency}**, using the same tax rate, debt share and equity risk premium."
    + (" The selected country is the U.S., so both columns should match. Pick another country "
       "in the sidebar to compare currencies." if country == "United States" else "")
)


def status_pill(src: dict, manual: bool = False) -> str:
    if manual:
        return '<span class="pill man">manual</span>'
    if src["status"] == "live":
        return '<span class="pill live">live</span>'
    return '<span class="pill snap">snapshot</span>'


c1, c2, c3, c4 = st.columns(4)
cards = [
    (c1, "Unlevered beta", f"{beta_u:.3f}", f"Damodaran · data as of {betas_src['as_of']}",
     status_pill(betas_src, beta_man)),
    (c2, "USD BBB spread", pct(spread_usd), f"Damodaran · {bbb_src['as_of']}",
     status_pill(bbb_src, spread_man)),
    (c3, f"Tax rate ({tax_label})", pct(tax_rate),
     f"OECD · {(tax_fc_year if use_fc_tax else tax_us_year) or 'manual'}",
     status_pill(tax_src, tax_fc_man if use_fc_tax else tax_us_man)),
    (c4, "10Y yields US / " + ("US" if same_ccy else currency),
     f"{pct(rf_us)} / {pct(rf_fc)}",
     f"Trading Economics · {us_y_src['as_of']}" + ("" if same_ccy else f" / {fc_y_src['as_of']}"),
     status_pill(us_y_src, rf_us_man or rf_fc_man) if fc_y_src["status"] == us_y_src["status"]
     else status_pill(fc_y_src, rf_fc_man)),
]
for col, lbl, val, meta, pill in cards:
    col.markdown(f'<div class="src-card"><div class="lbl">{lbl}{pill}</div>'
                 f'<div class="val">{val}</div><div class="meta">{meta}</div></div>',
                 unsafe_allow_html=True)

any_snapshot = any(s["status"] == "snapshot" for s in (betas_src, bbb_src, tax_src, us_y_src, fc_y_src))
if any_snapshot:
    st.warning("At least one live download failed, so the app is using a dated snapshot for it "
               "(marked *snapshot*). See the **Data sources** tab for details, or press "
               "**Refresh data** to try again.", icon="⚠️")
if use_fc_tax and fc_tax_missing and not tax_fc_man:
    st.warning(f"OECD has no tax rate for {country}; the U.S. rate is being used until you enter one.")

st.write("")
tab_res, tab_all, tab_method, tab_checks, tab_src = st.tabs(
    ["Results", "All industries", "Assumptions & method", "Checks", "Data sources"])

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
with tab_res:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("WACC in USD", pct(res.wacc_usd))
    sel = " (selected country)" if same_ccy else ""
    m2.metric(f"WACC in {currency}{sel}", pct(res.wacc_fc),
              delta=None if same_ccy else f"{(res.wacc_fc - res.wacc_usd) * 100:+.2f} pp vs USD",
              delta_color="off")
    m3.metric("Cost of equity in USD", pct(res.ke_usd))
    m4.metric(f"Cost of equity in {currency}{sel}", pct(res.ke_fc))

    rows = [
        ("Risk-free rate (10-year government yield)", rf_us, rf_fc, ""),
        ("× Levered beta", res.levered_beta, res.levered_beta, "beta"),
        ("Cost of equity", res.ke_usd, res.ke_fc, ""),
        ("BBB default spread" + ("" if same_ccy else
                                 (" (manual)" if res.spread_fc_is_override else " (auto for FC)")),
         spread_usd, res.spread_fc, ""),
        ("Pre-tax cost of debt", res.kd_usd, res.kd_fc, ""),
        (f"After-tax cost of debt (tax {pct(tax_rate)})", res.kd_usd_after_tax, res.kd_fc_after_tax, ""),
        (f"Weights: equity {pct(1 - debt_share, 0)} / debt {pct(debt_share, 0)}", None, None, "w"),
        ("WACC", res.wacc_usd, res.wacc_fc, "total"),
    ]
    html = [f'<table class="cmp"><tr><th></th><th style="color:{USD_COLOR}">USD</th>'
            f'<th style="color:{FC_COLOR}">{fc_name}</th><th>Difference</th></tr>']
    for label, a, b, kind in rows:
        if kind == "w":
            html.append(f'<tr><td colspan="4" style="opacity:.75">{label}</td></tr>')
            continue
        if kind == "beta":
            html.append(f'<tr><td>{label}</td><td class="num">{a:.3f}</td>'
                        f'<td class="num">{b:.3f}</td><td class="num">same</td></tr>')
            continue
        cls = ' class="total"' if kind == "total" else ""
        html.append(f'<tr{cls}><td>{label}</td><td class="num">{pct(a, 3)}</td>'
                    f'<td class="num">{pct(b, 3)}</td><td class="num">{round((b - a) * 100, 6) + 0.0:+.3f} pp</td></tr>')
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)

    st.caption(
        f"Shared inputs: unlevered beta {beta_u:.4f} → levered beta {res.levered_beta:.4f} "
        f"(D/E = {res.de_ratio:.4f}); tax rate {pct(tax_rate, 3)} ({tax_label}); "
        f"equity risk premium {pct(erp)}; debt share {pct(debt_share, 0)}; "
        f"conversion: {method_label.split(' (')[0].lower()}."
    )

    with st.expander("Show the math with these numbers"):
        g = (1 + rf_fc) / (1 + rf_us)
        conv = (f"(1 + {pct(res.ke_usd, 4)}) × (1 + {pct(rf_fc, 4)}) / (1 + {pct(rf_us, 4)}) − 1"
                if method == "multiplicative" else
                f"{pct(res.ke_usd, 4)} + ({pct(rf_fc, 4)} − {pct(rf_us, 4)})")
        st.markdown(f"""
1. **Debt-to-equity:** D/E = {debt_share:.2f} / {1 - debt_share:.2f} = **{res.de_ratio:.4f}**
2. **Levered beta:** βL = βU × [1 + (1 − t) × D/E] = {beta_u:.4f} × [1 + (1 − {tax_rate:.4f}) × {res.de_ratio:.4f}] = **{res.levered_beta:.4f}**
3. **USD cost of equity:** rf_US + βL × ERP = {pct(rf_us, 4)} + {res.levered_beta:.4f} × {pct(erp, 2)} = **{pct(res.ke_usd, 4)}**
4. **USD cost of debt:** rf_US + BBB spread = {pct(rf_us, 4)} + {pct(spread_usd, 2)} = **{pct(res.kd_usd, 4)}**; after tax × (1 − {tax_rate:.4f}) = **{pct(res.kd_usd_after_tax, 4)}**
5. **USD WACC:** {1 - debt_share:.2f} × {pct(res.ke_usd, 4)} + {debt_share:.2f} × {pct(res.kd_usd_after_tax, 4)} = **{pct(res.wacc_usd, 4)}**
6. **{currency} cost of equity:** {conv} = **{pct(res.ke_fc, 4)}**
7. **{currency} BBB spread:** {"manual input" if res.spread_fc_is_override else f"auto = USD cost of debt converted the same way ({pct(res.spread_fc_auto + rf_fc, 4)}) − rf_FC ({pct(rf_fc, 4)})"} = **{pct(res.spread_fc, 4)}**
8. **{currency} cost of debt:** rf_FC + spread = {pct(rf_fc, 4)} + {pct(res.spread_fc, 4)} = **{pct(res.kd_fc, 4)}**; after tax = **{pct(res.kd_fc_after_tax, 4)}**
9. **{currency} WACC:** {1 - debt_share:.2f} × {pct(res.ke_fc, 4)} + {debt_share:.2f} × {pct(res.kd_fc_after_tax, 4)} = **{pct(res.wacc_fc, 4)}**

Interest-rate ratio (1 + rf_FC)/(1 + rf_US) = {g:.6f}
""")

    # ---- downloads ----
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results_df = pd.DataFrame([
        ("Industry", industry, ""), ("Selected country", country, ""), ("Currency", currency, ""),
        ("Project tax rate used", tax_rate, tax_label), ("Equity risk premium", erp, ""),
        ("Debt share D/(D+E)", debt_share, ""), ("D/E", res.de_ratio, ""),
        ("Unlevered beta", beta_u, "manual" if beta_man else betas_src["status"]),
        ("Levered beta", res.levered_beta, ""),
        ("U.S. 10Y yield", rf_us, "manual" if rf_us_man else us_y_src["status"]),
        (f"{country} 10Y yield", rf_fc, "manual" if rf_fc_man else fc_y_src["status"]),
        ("USD BBB spread", spread_usd, "manual" if spread_man else bbb_src["status"]),
        (f"{currency} BBB spread", res.spread_fc, "manual" if res.spread_fc_is_override else "auto"),
        ("Cost of equity USD", res.ke_usd, ""), (f"Cost of equity {currency}", res.ke_fc, ""),
        ("Pre-tax cost of debt USD", res.kd_usd, ""), (f"Pre-tax cost of debt {currency}", res.kd_fc, ""),
        ("After-tax cost of debt USD", res.kd_usd_after_tax, ""),
        (f"After-tax cost of debt {currency}", res.kd_fc_after_tax, ""),
        ("WACC USD", res.wacc_usd, ""), (f"WACC {currency}", res.wacc_fc, ""),
        ("Conversion method", method, ""),
    ], columns=["Item", "Value", "Note"])

    all_df = coc.all_industries(betas_df, Inputs(**{**inputs.__dict__}))
    all_export = all_df.rename(columns={"Cost of equity (FC)": f"Cost of equity ({currency}, {country})",
                                        "WACC (FC)": f"WACC ({currency}, {country})"})
    sources_df = pd.DataFrame([
        ("Industry unlevered betas", "Damodaran", betas_src["source"], betas_src["as_of"],
         betas_src["fetched_at"], betas_src["status"]),
        ("USD BBB (Baa2) spread", "Damodaran", bbb_src["source"], bbb_src["as_of"],
         bbb_src["fetched_at"], bbb_src["status"]),
        ("Corporate tax rates", "OECD", tax_src["source"], tax_src["as_of"],
         tax_src["fetched_at"], tax_src["status"]),
        ("U.S. 10Y yield", "Trading Economics", us_y_src["source"], us_y_src["as_of"],
         us_y_src["fetched_at"], us_y_src["status"]),
        (f"{country} 10Y yield", "Trading Economics", fc_y_src["source"], fc_y_src["as_of"],
         fc_y_src["fetched_at"], fc_y_src["status"]),
    ], columns=["Data", "Provider", "URL", "Data as of", "Fetched at", "Status"])

    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        results_df.to_excel(xw, sheet_name="Results", index=False)
        all_export.to_excel(xw, sheet_name="All industries", index=False)
        sources_df.to_excel(xw, sheet_name="Sources", index=False)
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("⬇️ Results (CSV)", results_df.to_csv(index=False).encode(),
                       f"cost_of_capital_{country.replace(' ', '_')}_{stamp}.csv", "text/csv",
                       width="stretch")
    d2.download_button("⬇️ Everything (Excel)", xbuf.getvalue(),
                       f"cost_of_capital_{country.replace(' ', '_')}_{stamp}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")

# ---------------------------------------------------------------------------
# All industries
# ---------------------------------------------------------------------------
with tab_all:
    st.markdown(f"Every Damodaran industry under the same settings (country **{country}**, "
                f"tax {pct(tax_rate)}, ERP {pct(erp)}, debt share {pct(debt_share, 0)}). "
                f"Only the unlevered beta changes from row to row.")
    sort_by = st.radio("Sort by", ["WACC (USD)", "Industry"], horizontal=True)
    view = all_df.sort_values(sort_by, ascending=(sort_by == "Industry")).reset_index(drop=True)

    fig = go.Figure()
    order = view["Industry"].tolist()[::-1]
    v = view.set_index("Industry").loc[order]
    fig.add_trace(go.Scatter(
        x=v["WACC (USD)"] * 100, y=order, mode="markers", name="WACC in USD",
        marker=dict(color=USD_COLOR, size=9, line=dict(color="white", width=2)),
        hovertemplate="%{y}<br>WACC USD: %{x:.2f}%<extra></extra>"))
    if not same_ccy:
        fig.add_trace(go.Scatter(
            x=v["WACC (FC)"] * 100, y=order, mode="markers", name=f"WACC in {currency}",
            marker=dict(color=FC_COLOR, size=9, line=dict(color="white", width=2)),
            hovertemplate="%{y}<br>WACC " + currency + ": %{x:.2f}%<extra></extra>"))
    fig.add_hline(y=industry, line=dict(color="rgba(128,128,128,.35)", width=14))
    fig.update_layout(
        title=dict(text="WACC by industry (%)", x=0, y=0.995, yanchor="top", font=dict(size=15)),
        height=max(500, 16 * len(order) + 100), margin=dict(l=10, r=10, t=70, b=10),
        xaxis=dict(side="top", gridcolor="rgba(128,128,128,.15)", zeroline=False, ticksuffix="%"),
        yaxis=dict(tickfont=dict(size=11), gridcolor="rgba(128,128,128,.08)",
                   range=[-0.7, len(order) - 0.3], autorange=False),
        legend=dict(orientation="h", x=1, xanchor="right", y=0.995, yanchor="top", yref="container"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", hovermode="closest",
    )

    tab_tbl, tab_chart = st.tabs(["Table", "Chart"])
    with tab_chart:
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Grey band = selected industry ({industry}). Hover a dot for its value.")
    with tab_tbl:
        fmt = view.copy()
        for c in ["Cost of equity (USD)", "Cost of equity (FC)", "WACC (USD)", "WACC (FC)"]:
            fmt[c] = fmt[c] * 100
        tag = "selected: USD" if same_ccy else currency
        fmt = fmt.rename(columns={"Cost of equity (FC)": f"Cost of equity ({tag})",
                                  "WACC (FC)": f"WACC ({tag})"})
        st.dataframe(
            fmt.style.apply(lambda r: ["background-color: rgba(42,120,214,.15)"
                                       if r["Industry"] == industry else "" for _ in r], axis=1),
            hide_index=True, width="stretch", height=600,
            column_config={c: st.column_config.NumberColumn(format="%.2f%%") for c in
                           ["Cost of equity (USD)", f"Cost of equity ({tag})",
                            "WACC (USD)", f"WACC ({tag})"]}
            | {"Unlevered beta": st.column_config.NumberColumn(format="%.3f"),
               "Levered beta": st.column_config.NumberColumn(format="%.3f")},
        )
        st.download_button("⬇️ All industries (CSV)", all_export.to_csv(index=False).encode(),
                           f"all_industries_{country.replace(' ', '_')}_{stamp}.csv", "text/csv")

# ---------------------------------------------------------------------------
# Assumptions & method
# ---------------------------------------------------------------------------
with tab_method:
    st.markdown(r"""
### The idea in one paragraph
A project's cost of capital is the return investors need to fund it. We first work it out in
**U.S. dollars**, where the inputs (betas, the equity risk premium, the BBB spread) are measured.
Then we translate those dollar rates into the **foreign currency**. The only thing that changes
between the two currencies is expected inflation, which shows up in the gap between the two
countries' 10-year government bond yields. Business risk (beta), leverage, taxes and the equity
risk premium are held the same.

### Step by step
**1. Relever the industry beta** (Hamada). Damodaran's *unlevered* beta is the risk of the
business with no debt. Adding debt makes equity riskier:
""")
    st.latex(r"\frac{D}{E} = \frac{w_D}{1-w_D}, \qquad \beta_L = \beta_U\left[1 + (1-t)\frac{D}{E}\right]")
    st.markdown("**2. Costs in USD** (CAPM for equity, Treasury plus BBB spread for debt):")
    st.latex(r"k_E^{USD} = r_f^{US} + \beta_L \times ERP \qquad k_D^{USD} = r_f^{US} + s_{BBB}^{USD}")
    st.markdown("**3. Translate to the foreign currency** using the ratio of the two "
                "risk-free rates (interest rate parity / international Fisher effect):")
    st.latex(r"1 + k^{FC} = (1 + k^{USD}) \times \frac{1 + r_f^{FC}}{1 + r_f^{US}}")
    st.markdown("This is applied to the cost of equity and to the cost of debt. The foreign-currency "
                "BBB spread is then whatever is left over the foreign risk-free rate:")
    st.latex(r"s^{FC} = k_D^{FC} - r_f^{FC} = s^{USD}\times\frac{1+r_f^{FC}}{1+r_f^{US}} "
             r"\quad\text{(unless you override it)}")
    st.markdown("**4. WACC** in each currency, with the same weights and the same tax rate:")
    st.latex(r"WACC = (1-w_D)\,k_E + w_D\,k_D\,(1-t)")
    st.markdown("""
### Main assumptions
- **Same business risk in both currencies.** The project's beta, the equity risk premium and the
  debt share are identical; only the currency of measurement changes. No extra country risk premium
  is added.
- **10-year government yields are the risk-free rates.** Long-dated yields match long-lived projects.
  For some countries the government itself carries default risk, so its yield is not truly risk-free.
- **The yield gap reflects expected inflation,** so translating with the yield ratio keeps real
  (inflation-adjusted) required returns equal across currencies.
- **One tax rate for the whole project.** You choose the U.S. or the selected country's combined
  statutory rate (central + local). It is used both to relever beta and for the debt tax shield.
- **BBB rating.** The project borrows at the spread of a Baa2/BBB large non-financial firm
  (Damodaran's table, based on U.S. bonds).
- **Debt share is a target market-value weight,** not the industry's current leverage.
- **Additive option.** The advanced setting `r_FC = r_USD + (rf_FC − rf_US)` is a simpler
  approximation; it is close to the exact version when rates are low and drifts apart when the
  foreign yield is high (for example Turkey).
""")

# ---------------------------------------------------------------------------
# Checks (run live on the current settings)
# ---------------------------------------------------------------------------
with tab_checks:
    st.markdown("These checks re-run the model with one setting changed and everything else held "
                "as it is in the sidebar.")
    tol = 1e-12

    eq = compute(Inputs(**{**inputs.__dict__, "rf_fc": rf_us, "spread_fc_override": None}))
    c_eq = (abs(eq.ke_fc - eq.ke_usd) < tol and abs(eq.kd_fc - eq.kd_usd) < tol
            and abs(eq.wacc_fc - eq.wacc_usd) < tol)

    z = compute(Inputs(**{**inputs.__dict__, "debt_share": 0.0}))
    c_z = abs(z.wacc_usd - z.ke_usd) < tol and abs(z.wacc_fc - z.ke_fc) < tol

    o1 = compute(Inputs(**{**inputs.__dict__, "spread_fc_override": None}))
    o2 = compute(Inputs(**{**inputs.__dict__, "spread_fc_override": o1.spread_fc_auto + 0.02}))
    c_o = (o1.ke_usd == o2.ke_usd and o1.kd_usd == o2.kd_usd and o1.wacc_usd == o2.wacc_usd
           and o2.wacc_fc != o1.wacc_fc)

    checks = pd.DataFrame([
        ("1. Equal bond yields → equal results",
         f"Set {country} yield = U.S. yield ({pct(rf_us, 3)}), FC spread override off",
         f"WACC USD {pct(eq.wacc_usd, 4)} vs FC {pct(eq.wacc_fc, 4)}; "
         f"ke {pct(eq.ke_usd, 4)} vs {pct(eq.ke_fc, 4)}", "✅ Pass" if c_eq else "❌ Fail"),
        ("2. Zero debt → WACC = cost of equity", "Debt share = 0%",
         f"USD: {pct(z.wacc_usd, 4)} = {pct(z.ke_usd, 4)}; FC: {pct(z.wacc_fc, 4)} = {pct(z.ke_fc, 4)}",
         "✅ Pass" if c_z else "❌ Fail"),
        ("3. Overriding the FC spread leaves USD unchanged",
         f"FC spread {pct(o1.spread_fc_auto, 3)} → {pct(o1.spread_fc_auto + 0.02, 3)}",
         f"USD WACC {pct(o1.wacc_usd, 4)} → {pct(o2.wacc_usd, 4)}; "
         f"FC WACC {pct(o1.wacc_fc, 4)} → {pct(o2.wacc_fc, 4)}", "✅ Pass" if c_o else "❌ Fail"),
    ], columns=["Check", "What changes", "Result", "Status"])
    checks = checks[["Status", "Check", "What changes", "Result"]]
    st.dataframe(checks, hide_index=True, width="stretch",
                 column_config={"Result": st.column_config.TextColumn(width="large")})

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
with tab_src:
    show = sources_df.copy()
    st.dataframe(show, hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn("URL", display_text="open")})
    st.markdown(f"""
- **Betas:** [Damodaran, betas.xls]({coc.BETAS_URL}), sheet *Industry Averages*, column
  *Unlevered beta* (U.S. companies).
- **BBB spread:** [Damodaran, ratings.html]({coc.RATINGS_URL}), table for large non-financial
  service firms, row *Baa2/BBB*.
- **Tax rates:** OECD Corporate Tax Statistics, combined (central + sub-central) statutory corporate
  income tax rate. The [2024 report chapter]({coc.OECD_REPORT_URL}) shows this as a chart image, so
  the app reads the same OECD database through the OECD Data Explorer API (dataset
  `DSD_TAX_CIT@DF_CIT`, measure `CIT_C`) and uses the latest year available for each country.
- **10-year yields:** [Trading Economics bonds page]({coc.TE_BONDS_URL}); if that table can't be
  read, the app reads the country's own Trading Economics page.
- Downloads are cached for 6 hours. **Refresh data** clears the cache. If a site can't be reached,
  the app falls back to a snapshot saved on 16 Nov 2026 and labels it *snapshot*.
""")
    errs = [(n, s["error"]) for n, s in [("Betas", betas_src), ("BBB spread", bbb_src),
            ("Tax rates", tax_src), ("U.S. yield", us_y_src), (f"{country} yield", fc_y_src)]
            if s.get("error")]
    if errs:
        with st.expander("Download errors"):
            for n, e in errs:
                st.write(f"**{n}:** {e}")

    with st.expander("All 10-year yields (Trading Economics)"):
        bt = get_bond_table()
        if bt["status"] == "live":
            st.caption(f"Live · as of {bt['as_of']} · fetched {bt['fetched_at']}")
        else:
            st.caption("Trading Economics only serves its full bonds table to browsers, so this list "
                       "is the snapshot saved on 16 Nov 2026. The two yields used in the calculation "
                       "(see the table above) are downloaded live from each country's own "
                       "Trading Economics page.")
        st.dataframe(bt["data"], hide_index=True, width="stretch")
    with st.expander("All corporate tax rates (OECD)"):
        st.dataframe(tax_src["data"], hide_index=True, width="stretch")

st.caption("Built for Kellogg International Finance, fall 2026. Educational use; not investment advice.")
