# International Cost of Capital

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in U.S. dollars
and in the currency of any selected country**, using live market and tax data.

Built for the International Finance course at Kellogg School of Management (fall 2026) by
Manasa Nandini Manimozhi.

## What it does

- Pick an **industry** (Damodaran's 94 U.S. industries plus market totals) and a **target country**
  (55 countries and the Euro area covered by Trading Economics' 10-year bond table).
- Adjust the **equity risk premium** and the **target debt share**.
- Switch the project **tax rate** between the U.S. rate and the selected country's rate. The same
  rate is used in both currencies.
- **Override** any downloaded input: unlevered beta, U.S. and foreign tax rates, USD BBB spread, and
  both 10-year yields.
- The **foreign-currency BBB spread** is calculated automatically; a toggle lets you type your own.
- See results **side by side** (USD vs. foreign currency), a step-by-step "show the math" panel,
  an **all-industry comparison** (table and chart), live **sanity checks**, and **downloads**
  (CSV and an Excel workbook with inputs, results, all industries and sources).

**Defaults on opening:** Software (System & Application), United States as the selected country,
U.S. tax rate, 6.5% equity risk premium, 30% debt.

## Formulas

All rates are annual. `w_D` is the debt share D/(D+E), `t` the project tax rate.

| Step | Formula |
|---|---|
| Debt-to-equity | `D/E = w_D / (1 − w_D)` |
| Levered beta (Hamada) | `βL = βU × [1 + (1 − t) × D/E]` |
| USD cost of equity (CAPM) | `kE_USD = rf_US + βL × ERP` |
| USD cost of debt | `kD_USD = rf_US + s_BBB` |
| Translate to foreign currency | `1 + k_FC = (1 + k_USD) × (1 + rf_FC) / (1 + rf_US)` |
| Foreign BBB spread (auto) | `s_FC = kD_FC − rf_FC` (or a manual value) |
| Foreign cost of debt | `kD_FC = rf_FC + s_FC` |
| WACC (each currency) | `WACC = (1 − w_D) × kE + w_D × kD × (1 − t)` |

An "Advanced" setting switches the translation to the additive approximation
`k_FC = k_USD + (rf_FC − rf_US)`.

## Data sources

| Input | Source | How it is read |
|---|---|---|
| Unlevered beta | [Damodaran, betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Sheet *Industry Averages*, column *Unlevered beta* |
| USD BBB spread | [Damodaran, ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | Large non-financial firms table, row *Baa2/BBB* |
| Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | Combined statutory CIT rate (measure `CIT_C`) from the OECD Data Explorer API, dataset `DSD_TAX_CIT@DF_CIT`, latest year per country |
| 10-year government yields | [Trading Economics](https://tradingeconomics.com/bonds) | Bonds table; falls back to each country's government-bond-yield page |

The OECD report chapter shows its rates as a chart image, so the app reads the same OECD
database through its public API instead. No API keys are needed.

Downloads are cached for 6 hours; the **Refresh data** button clears the cache. Every source shows
its data date, the time it was fetched, and whether it is *live*, a *snapshot* or a *manual*
override. If a website can't be reached, the app uses the dated snapshot in `data/`
(saved 16 November 2026) and says so on screen.

## Checks built into the app

The *Checks* tab re-runs the model to confirm:
1. equal 10-year yields give equal USD and foreign-currency results (spread override off);
2. zero debt makes WACC equal the cost of equity;
3. overriding the foreign-currency spread leaves all USD results unchanged.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Files

- `app.py` – Streamlit interface
- `cost_of_capital.py` – the math and the data downloaders
- `data/` – dated snapshots used only when a live download fails
- `requirements.txt` – Python packages
- `.streamlit/config.toml` – theme
