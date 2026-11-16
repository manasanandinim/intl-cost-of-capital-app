"""
Core logic for the International Cost of Capital app.

Two parts, kept separate from the Streamlit UI so they can be tested on their own:
  1. compute(): the cost of equity, cost of debt and WACC math, in USD and in
     a foreign currency.
  2. fetch_*(): live data downloads (Damodaran, OECD, Trading Economics), each
     with a dated snapshot fallback stored in ./data so the app still opens if a
     website is down or blocks the request.

All rates inside this module are decimals (0.065 = 6.5%).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_REPORT_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
# Same OECD Corporate Tax Statistics database, in machine-readable form.
# Measure CIT_C = combined (central + sub-central) statutory corporate income tax rate.
OECD_SDMX_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/"
    ".A.CIT_C.ST.PT_INC_TAX.S13._Z._Z._Z?startPeriod={start}&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 20

# ---------------------------------------------------------------------------
# Countries covered by the Trading Economics 10-year bond table
# name -> (Trading Economics URL slug, ISO3 code used by OECD or None, currency)
# ---------------------------------------------------------------------------
COUNTRIES: dict[str, tuple[str, str | None, str]] = {
    "United States": ("united-states", "USA", "USD"),
    "United Kingdom": ("united-kingdom", "GBR", "GBP"),
    "Euro area": ("euro-area", None, "EUR"),
    "Germany": ("germany", "DEU", "EUR"),
    "France": ("france", "FRA", "EUR"),
    "Italy": ("italy", "ITA", "EUR"),
    "Spain": ("spain", "ESP", "EUR"),
    "Netherlands": ("netherlands", "NLD", "EUR"),
    "Belgium": ("belgium", "BEL", "EUR"),
    "Austria": ("austria", "AUT", "EUR"),
    "Ireland": ("ireland", "IRL", "EUR"),
    "Portugal": ("portugal", "PRT", "EUR"),
    "Greece": ("greece", "GRC", "EUR"),
    "Finland": ("finland", "FIN", "EUR"),
    "Slovakia": ("slovakia", "SVK", "EUR"),
    "Slovenia": ("slovenia", "SVN", "EUR"),
    "Lithuania": ("lithuania", "LTU", "EUR"),
    "Croatia": ("croatia", "HRV", "EUR"),
    "Switzerland": ("switzerland", "CHE", "CHF"),
    "Sweden": ("sweden", "SWE", "SEK"),
    "Norway": ("norway", "NOR", "NOK"),
    "Denmark": ("denmark", "DNK", "DKK"),
    "Iceland": ("iceland", "ISL", "ISK"),
    "Poland": ("poland", "POL", "PLN"),
    "Czech Republic": ("czech-republic", "CZE", "CZK"),
    "Hungary": ("hungary", "HUN", "HUF"),
    "Romania": ("romania", "ROU", "RON"),
    "Turkey": ("turkey", "TUR", "TRY"),
    "Russia": ("russia", None, "RUB"),
    "Japan": ("japan", "JPN", "JPY"),
    "China": ("china", "CHN", "CNY"),
    "India": ("india", "IND", "INR"),
    "South Korea": ("south-korea", "KOR", "KRW"),
    "Taiwan": ("taiwan", None, "TWD"),
    "Hong Kong": ("hong-kong", "HKG", "HKD"),
    "Singapore": ("singapore", "SGP", "SGD"),
    "Indonesia": ("indonesia", "IDN", "IDR"),
    "Malaysia": ("malaysia", "MYS", "MYR"),
    "Thailand": ("thailand", "THA", "THB"),
    "Philippines": ("philippines", "PHL", "PHP"),
    "Vietnam": ("vietnam", "VNM", "VND"),
    "Pakistan": ("pakistan", "PAK", "PKR"),
    "Israel": ("israel", "ISR", "ILS"),
    "Australia": ("australia", "AUS", "AUD"),
    "New Zealand": ("new-zealand", "NZL", "NZD"),
    "Canada": ("canada", "CAN", "CAD"),
    "Mexico": ("mexico", "MEX", "MXN"),
    "Brazil": ("brazil", "BRA", "BRL"),
    "Chile": ("chile", "CHL", "CLP"),
    "Colombia": ("colombia", "COL", "COP"),
    "Peru": ("peru", "PER", "PEN"),
    "South Africa": ("south-africa", "ZAF", "ZAR"),
    "Nigeria": ("nigeria", "NGA", "NGN"),
    "Kenya": ("kenya", "KEN", "KES"),
    "Zambia": ("zambia", "ZMB", "ZMW"),
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 0.065
DEFAULT_DEBT_SHARE = 0.30


# ---------------------------------------------------------------------------
# 1. The math
# ---------------------------------------------------------------------------
@dataclass
class Inputs:
    unlevered_beta: float
    tax_rate: float            # project tax rate (U.S. or selected country)
    erp: float                 # equity risk premium (USD)
    debt_share: float          # D / (D + E)
    rf_usd: float              # U.S. 10-year Treasury yield
    rf_fc: float               # selected country's 10-year government yield
    spread_usd: float          # USD BBB default spread
    spread_fc_override: float | None = None   # None = auto-calculate
    method: str = "multiplicative"            # or "additive"


@dataclass
class Result:
    de_ratio: float
    levered_beta: float
    # USD
    ke_usd: float
    kd_usd: float
    kd_usd_after_tax: float
    wacc_usd: float
    # foreign currency
    ke_fc: float
    spread_fc: float
    spread_fc_auto: float
    spread_fc_is_override: bool
    kd_fc: float
    kd_fc_after_tax: float
    wacc_fc: float
    notes: list[str] = field(default_factory=list)


def to_foreign(rate_usd: float, rf_usd: float, rf_fc: float, method: str) -> float:
    """Convert a USD rate into the foreign currency using the two risk-free rates.

    multiplicative (exact parity / international Fisher):
        (1 + r_FC) = (1 + r_USD) * (1 + rf_FC) / (1 + rf_USD)
    additive (linear approximation):
        r_FC = r_USD + (rf_FC - rf_USD)
    """
    if method == "additive":
        return rate_usd + (rf_fc - rf_usd)
    return (1 + rate_usd) * (1 + rf_fc) / (1 + rf_usd) - 1


def compute(x: Inputs) -> Result:
    d = x.debt_share
    if not 0 <= d < 1:
        raise ValueError("Debt share must be at least 0% and below 100%.")
    e = 1 - d
    de = d / e

    # Hamada: relever the industry's asset beta at the target debt/equity mix
    beta_l = x.unlevered_beta * (1 + (1 - x.tax_rate) * de)

    # --- USD ---
    ke_usd = x.rf_usd + beta_l * x.erp
    kd_usd = x.rf_usd + x.spread_usd
    kd_usd_at = kd_usd * (1 - x.tax_rate)
    wacc_usd = e * ke_usd + d * kd_usd_at

    # --- Foreign currency ---
    ke_fc = to_foreign(ke_usd, x.rf_usd, x.rf_fc, x.method)
    kd_fc_auto = to_foreign(kd_usd, x.rf_usd, x.rf_fc, x.method)
    spread_fc_auto = kd_fc_auto - x.rf_fc
    if x.spread_fc_override is None:
        spread_fc, is_override = spread_fc_auto, False
    else:
        spread_fc, is_override = x.spread_fc_override, True
    kd_fc = x.rf_fc + spread_fc
    kd_fc_at = kd_fc * (1 - x.tax_rate)
    wacc_fc = e * ke_fc + d * kd_fc_at

    return Result(
        de_ratio=de, levered_beta=beta_l,
        ke_usd=ke_usd, kd_usd=kd_usd, kd_usd_after_tax=kd_usd_at, wacc_usd=wacc_usd,
        ke_fc=ke_fc, spread_fc=spread_fc, spread_fc_auto=spread_fc_auto,
        spread_fc_is_override=is_override, kd_fc=kd_fc, kd_fc_after_tax=kd_fc_at,
        wacc_fc=wacc_fc,
    )


def all_industries(betas: pd.DataFrame, base: Inputs) -> pd.DataFrame:
    """Run compute() for every industry with the same shared settings."""
    rows = []
    for _, r in betas.iterrows():
        x = Inputs(**{**base.__dict__, "unlevered_beta": float(r["unlevered_beta"])})
        res = compute(x)
        rows.append({
            "Industry": r["industry"],
            "Firms": int(r["n_firms"]) if pd.notna(r["n_firms"]) else None,
            "Unlevered beta": r["unlevered_beta"],
            "Levered beta": res.levered_beta,
            "Cost of equity (USD)": res.ke_usd,
            "Cost of equity (FC)": res.ke_fc,
            "WACC (USD)": res.wacc_usd,
            "WACC (FC)": res.wacc_fc,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Data fetchers. Each returns a dict with the data plus provenance:
#    status = "live" or "snapshot", as_of = date of the data, source = URL,
#    fetched_at = when this app downloaded it, error = why live failed (if so).
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    if r.status_code != 200 or not r.content:
        raise RuntimeError(f"HTTP {r.status_code} with empty body (likely a bot check)")
    return r


# --- Damodaran industry betas ----------------------------------------------
def parse_betas_xls(content: bytes) -> tuple[pd.DataFrame, str]:
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Industry Averages",
                        header=None, engine="xlrd")
    as_of = ""
    for i in range(min(10, len(raw))):
        if str(raw.iloc[i, 0]).strip().lower().startswith("date updated"):
            v = raw.iloc[i, 1]
            as_of = pd.to_datetime(v).strftime("%Y-%m-%d") if pd.notna(v) else ""
            break
    hdr_idx = raw.index[raw[0].astype(str).str.strip() == "Industry Name"][0]
    t = raw.iloc[hdr_idx + 1:].copy()
    t.columns = [str(c).strip() for c in raw.iloc[hdr_idx]]
    t = t[t["Industry Name"].notna()]
    out = pd.DataFrame({
        "industry": t["Industry Name"].astype(str).str.strip(),
        "n_firms": pd.to_numeric(t["Number of firms"], errors="coerce"),
        "levered_beta": pd.to_numeric(t["Beta"], errors="coerce"),
        "de_ratio": pd.to_numeric(t["D/E Ratio"], errors="coerce"),
        "effective_tax_rate": pd.to_numeric(t["Effective Tax rate"], errors="coerce"),
        "unlevered_beta": pd.to_numeric(t["Unlevered beta"], errors="coerce"),
        "unlevered_beta_cash_corrected": pd.to_numeric(
            t["Unlevered beta corrected for cash"], errors="coerce"),
    })
    out = out[out["unlevered_beta"].notna()].reset_index(drop=True)
    if len(out) < 50:
        raise RuntimeError(f"Only {len(out)} industries parsed; file layout may have changed")
    return out, as_of


def fetch_betas() -> dict:
    try:
        df, as_of = parse_betas_xls(_get(BETAS_URL).content)
        return dict(data=df, status="live", as_of=as_of, source=BETAS_URL,
                    fetched_at=_now(), error=None)
    except Exception as exc:  # noqa: BLE001
        df = pd.read_csv(DATA_DIR / "damodaran_betas_snapshot.csv")
        return dict(data=df, status="snapshot", as_of="2026-01-05", source=BETAS_URL,
                    fetched_at=_now(), error=str(exc)[:200])


# --- Damodaran BBB spread ---------------------------------------------------
def parse_bbb_spread(html: str) -> tuple[float, str]:
    soup = BeautifulSoup(html, "lxml")
    spread = None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if "Baa2/BBB" in cells:
            i = cells.index("Baa2/BBB")   # first occurrence = large non-financial table
            spread = float(cells[i + 1].replace("%", "").strip()) / 100
            break
    if spread is None:
        raise RuntimeError("Baa2/BBB row not found")
    m = re.search(r"as of\s+([A-Za-z]+\s+\d{4})", soup.get_text(" "))
    return spread, (m.group(1) if m else "")


def fetch_bbb_spread() -> dict:
    try:
        spread, as_of = parse_bbb_spread(_get(RATINGS_URL).text)
        return dict(data=spread, status="live", as_of=as_of, source=RATINGS_URL,
                    fetched_at=_now(), error=None)
    except Exception as exc:  # noqa: BLE001
        return dict(data=0.0111, status="snapshot", as_of="January 2026",
                    source=RATINGS_URL, fetched_at=_now(), error=str(exc)[:200])


# --- OECD corporate tax rates ----------------------------------------------
def parse_oecd_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df = df[["REF_AREA", "Reference area", "TIME_PERIOD", "OBS_VALUE"]].dropna(subset=["OBS_VALUE"])
    df = df.sort_values("TIME_PERIOD").groupby("REF_AREA", as_index=False).last()
    df.columns = ["iso3", "jurisdiction", "year", "combined_statutory_cit_pct"]
    if len(df) < 30:
        raise RuntimeError(f"Only {len(df)} jurisdictions returned")
    return df


def fetch_tax_rates() -> dict:
    url = OECD_SDMX_URL.format(start=date.today().year - 4)
    try:
        df = parse_oecd_csv(_get(url).text)
        as_of = f"{int(df['year'].max())} rates (latest year per jurisdiction)"
        return dict(data=df, status="live", as_of=as_of, source=url,
                    fetched_at=_now(), error=None)
    except Exception as exc:  # noqa: BLE001
        df = pd.read_csv(DATA_DIR / "oecd_cit_snapshot.csv")
        return dict(data=df, status="snapshot", as_of="2026 rates (snapshot 2026-11-16)",
                    source=url, fetched_at=_now(), error=str(exc)[:200])


def tax_rate_for(country: str, tax_df: pd.DataFrame) -> tuple[float | None, int | None]:
    iso = COUNTRIES[country][1]
    if iso is None:
        return None, None
    row = tax_df[tax_df["iso3"] == iso]
    if row.empty:
        return None, None
    r = row.iloc[0]
    return float(r["combined_statutory_cit_pct"]) / 100, int(r["year"])


# --- Trading Economics 10-year government bond yields ----------------------
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def te_date(text: str, today: date | None = None) -> str:
    """'Nov/13' -> '2026-11-13'; an intraday time such as '12:05' -> today."""
    today = today or datetime.now(timezone.utc).date()
    text = text.strip()
    if ":" in text:
        return today.isoformat()
    m = re.match(r"([A-Za-z]{3})/(\d{1,2})", text)
    if not m or m.group(1) not in _MONTHS:
        return text
    d = date(today.year, _MONTHS[m.group(1)], int(m.group(2)))
    if d > today:
        d = date(today.year - 1, d.month, d.day)
    return d.isoformat()


def parse_te_bonds_table(html: str) -> pd.DataFrame:
    """Parse the region tables on tradingeconomics.com/bonds (rows carry data-symbol)."""
    soup = BeautifulSoup(html, "lxml")
    slug_to_name = {v[0]: k for k, v in COUNTRIES.items()}
    rows = {}
    for tr in soup.select("tr[data-symbol]"):
        a = tr.find("a", href=True)
        if not a:
            continue
        m = re.match(r"/([^/]+)/government-bond-yield", a["href"])
        if not m or m.group(1) not in slug_to_name:
            continue
        name = slug_to_name[m.group(1)]
        if name in rows:
            continue
        cell = tr.find("td", id="p")
        tds = tr.find_all("td")
        rows[name] = dict(country=name, yield_pct=float(cell.get_text(strip=True)),
                          as_of=te_date(tds[-1].get_text(strip=True)))
    if len(rows) < 10:
        raise RuntimeError(f"Only {len(rows)} countries parsed from the bonds table")
    return pd.DataFrame(rows.values())


def parse_te_country_page(html: str) -> tuple[float, str]:
    """Parse one country's /government-bond-yield page: the '10Y' row of its first table."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("No table on country page")
    for tr in table.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if tds and re.search(r"\b10Y\b", tds[0]):
            nums = [t for t in tds[1:] if re.fullmatch(r"-?\d+(\.\d+)?", t)]
            value = float(nums[0])
            as_of = te_date(tds[-1])
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                m = re.search(r"on ([A-Z][a-z]+ \d{1,2}, \d{4})", meta["content"])
                if m:
                    as_of = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
            return value, as_of
    raise RuntimeError("10Y row not found on country page")


def fetch_bond_table() -> dict:
    """All countries' 10Y yields. Tries the bonds page, then falls back to the snapshot."""
    errors = []
    for url in (TE_BONDS_URL + "?partial=markets", TE_BONDS_URL):
        try:
            df = parse_te_bonds_table(_get(url).text)
            return dict(data=df, status="live", as_of=df["as_of"].max(), source=TE_BONDS_URL,
                        fetched_at=_now(), error=None)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {str(exc)[:120]}")
    snap = pd.read_csv(DATA_DIR / "te_bonds_snapshot.csv")[["country", "yield_pct", "as_of"]]
    return dict(data=snap, status="snapshot", as_of="2026-11-16 (snapshot)",
                source=TE_BONDS_URL, fetched_at=_now(), error=" | ".join(errors))


def fetch_country_yield(country: str, bond_table: dict) -> dict:
    """One country's 10Y yield, as a dict with value (decimal) and provenance.

    Order: the live bonds table (4 decimals) -> the country's own page -> snapshot.
    """
    slug = COUNTRIES[country][0]
    page = TE_COUNTRY_URL.format(slug=slug)
    df = bond_table["data"]
    if bond_table["status"] == "live" and country in set(df["country"]):
        r = df[df["country"] == country].iloc[0]
        return dict(data=r["yield_pct"] / 100, status="live", as_of=r["as_of"],
                    source=TE_BONDS_URL, fetched_at=bond_table["fetched_at"], error=None)
    try:
        value, as_of = parse_te_country_page(_get(page).text)
        return dict(data=value / 100, status="live", as_of=as_of, source=page,
                    fetched_at=_now(), error=None)
    except Exception as exc:  # noqa: BLE001
        snap = pd.read_csv(DATA_DIR / "te_bonds_snapshot.csv")
        r = snap[snap["country"] == country].iloc[0]
        return dict(data=r["yield_pct"] / 100, status="snapshot", as_of=r["as_of"],
                    source=page, fetched_at=_now(), error=str(exc)[:200])
