import re

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from bs4 import BeautifulSoup

st.set_page_config(page_title="Ciclo de vida de la empresa", page_icon="📈", layout="centered")

PHASES = ["Introducción", "Crecimiento", "Madurez", "Declive"]
EMOJI = {"Introducción": "🌱", "Crecimiento": "🚀", "Madurez": "🏛️", "Declive": "📉"}


# ----------------------------------------------------------------- utilidades
def num(x):
    try:
        if x is None:
            return None
        f = float(x)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def fv_pct(fv, *keys):
    """Porcentaje de Finviz ('12.3%') -> 12.3"""
    for k in keys:
        s = str(fv.get(k, "")).strip()
        mt = re.match(r"^(-?[\d.,]+)%$", s)
        if mt:
            return float(mt.group(1).replace(",", ""))
    return None


def fv_num(fv, *keys):
    for k in keys:
        s = str(fv.get(k, "")).strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            continue
    return None


def fmt(v, suf="", dec=1):
    return "N/D" if v is None else f"{v:,.{dec}f}{suf}"


def series(df, names):
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s.sort_index()
    return None


# ------------------------------------------------------------------- datos
@st.cache_data(ttl=3600, show_spinner=False)
def get_yahoo(ticker):
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    try:
        inc = t.income_stmt
    except Exception:
        inc = None
    try:
        cf = t.cashflow
    except Exception:
        cf = None
    return info, inc, cf


@st.cache_data(ttl=3600, show_spinner=False)
def get_finviz(ticker):
    url = f"https://finviz.com/quote.ashx?t={ticker}&p=d"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="snapshot-table2")
        if not table:
            return {}
        cells = [td.get_text(strip=True) for td in table.find_all("td")]
        return dict(zip(cells[0::2], cells[1::2]))
    except Exception:
        return {}


# ----------------------------------------------------------------- métricas
def build_metrics(info, inc, cf, fv):
    m = {}
    rev = series(inc, ["Total Revenue", "Operating Revenue"])
    ni = series(inc, ["Net Income"])
    op = series(inc, ["Operating Income"])
    fcf_s = series(cf, ["Free Cash Flow"])

    # Crecimiento de ingresos
    q_yoy = num(info.get("revenueGrowth"))
    q_yoy = q_yoy * 100 if q_yoy is not None else fv_pct(fv, "Sales Q/Q")
    a_yoy = cagr = None
    if rev is not None and len(rev) >= 2 and rev.iloc[-2] > 0:
        a_yoy = (rev.iloc[-1] / rev.iloc[-2] - 1) * 100
    if rev is not None and len(rev) >= 3 and rev.iloc[0] > 0 and rev.iloc[-1] > 0:
        cagr = ((rev.iloc[-1] / rev.iloc[0]) ** (1 / (len(rev) - 1)) - 1) * 100
    vals = [v for v in (q_yoy, a_yoy, cagr) if v is not None]
    m["rev_q_yoy"], m["rev_a_yoy"], m["rev_cagr"] = q_yoy, a_yoy, cagr
    m["rev_growth"] = sum(vals) / len(vals) if vals else None

    # Márgenes
    def mg(key, fvkey):
        v = num(info.get(key))
        return v * 100 if v is not None else fv_pct(fv, fvkey)

    m["gross_margin"] = mg("grossMargins", "Gross Margin")
    m["op_margin"] = mg("operatingMargins", "Oper. Margin")
    m["net_margin"] = mg("profitMargins", "Profit Margin")
    if m["net_margin"] is None and ni is not None and rev is not None:
        m["net_margin"] = ni.iloc[-1] / rev.iloc[-1] * 100

    m["op_margin_delta"] = None
    if op is not None and rev is not None:
        j = pd.concat([op, rev], axis=1, keys=["op", "rev"]).dropna()
        if len(j) >= 2:
            mm = j["op"] / j["rev"] * 100
            m["op_margin_delta"] = mm.iloc[-1] - mm.iloc[0]

    # Flujo de caja libre
    fcf = num(info.get("freeCashflow"))
    trev = num(info.get("totalRevenue"))
    if fcf is not None and trev:
        m["fcf_margin"] = fcf / trev * 100
    elif fcf_s is not None and rev is not None:
        m["fcf_margin"] = fcf_s.iloc[-1] / rev.iloc[-1] * 100
    else:
        m["fcf_margin"] = None

    # Dividendos
    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
    rate = num(info.get("dividendRate"))
    m["price"] = price
    m["div_yield"] = (rate / price * 100) if (rate and price) else 0.0
    po = num(info.get("payoutRatio"))
    m["payout"] = po * 100 if po is not None else fv_pct(fv, "Payout")

    # Retornos, valoración, riesgo
    roe = num(info.get("returnOnEquity"))
    m["roe"] = roe * 100 if roe is not None else fv_pct(fv, "ROE")
    roa = num(info.get("returnOnAssets"))
    m["roa"] = roa * 100 if roa is not None else fv_pct(fv, "ROA")
    m["roi"] = fv_pct(fv, "ROIC", "ROI")
    m["pe"] = num(info.get("trailingPE")) or fv_num(fv, "P/E")
    m["fpe"] = num(info.get("forwardPE")) or fv_num(fv, "Forward P/E")
    m["peg"] = num(info.get("trailingPegRatio")) or fv_num(fv, "PEG")
    m["ps"] = num(info.get("priceToSalesTrailing12Months")) or fv_num(fv, "P/S")
    m["beta"] = num(info.get("beta")) or fv_num(fv, "Beta")
    de = num(info.get("debtToEquity"))
    m["de"] = de / 100 if de is not None else fv_num(fv, "Debt/Eq")

    eg = num(info.get("earningsGrowth"))
    m["eps_qq"] = eg * 100 if eg is not None else fv_pct(fv, "EPS Q/Q")
    m["eps_next5"] = fv_pct(fv, "EPS next 5Y")
    m["eps_past5"] = fv_pct(fv, "EPS past 5Y")
    m["sales_past5"] = fv_pct(fv, "Sales past 5Y")
    m["perf_year"] = fv_pct(fv, "Perf Year")
    m["inst_own"] = fv_pct(fv, "Inst Own")

    m["_rev_s"], m["_ni_s"] = rev, ni
    return m


# ------------------------------------------------------------------ scoring
def score_phases(m):
    tot = {p: 0.0 for p in PHASES}
    W = 0.0
    signals = []

    def add(w, d, label, val):
        nonlocal W
        W += w
        for p, x in zip(PHASES, d):
            tot[p] += w * x
        signals.append({"Indicador": label, "Valor": val, "Apunta a": PHASES[d.index(max(d))]})

    g = m["rev_growth"]
    if g is not None:
        d = ((.15, .85, 0, 0) if g >= 40 else (.05, .85, .1, 0) if g >= 20 else
             (0, .6, .35, .05) if g >= 10 else (0, .15, .8, .05) if g >= 3 else
             (0, 0, .7, .3) if g >= 0 else (0, 0, .15, .85))
        add(3, d, "Crecimiento de ingresos", fmt(g, "%"))

    nm = m["net_margin"]
    if nm is not None:
        d = ((.55, .15, 0, .3) if nm < 0 else (.1, .4, .35, .15) if nm < 10 else
             (0, .35, .55, .1) if nm < 25 else (0, .5, .5, 0))
        add(2, d, "Margen neto", fmt(nm, "%"))

    f = m["fcf_margin"]
    if f is not None:
        d = ((.6, .2, 0, .2) if f < 0 else (.05, .35, .45, .15) if f < 10 else
             (0, .4, .5, .1) if f < 20 else (0, .5, .5, 0))
        add(1.5, d, "Margen de FCF", fmt(f, "%"))

    dy, po = m["div_yield"], m["payout"]
    if po is not None and po > 80:
        d = (0, 0, .5, .5)
    elif (po is not None and po >= 30) or dy >= 1.5:
        d = (0, 0, .8, .2)
    elif dy >= 0.3 or (po is not None and po > 0):
        d = (0, .4, .55, .05)
    else:
        d = (.25, .65, .1, 0)
    add(2, d, "Dividendo / payout", f"{dy:.2f}% / {fmt(po, '%', 0)}")

    eg = m["eps_qq"]
    if eg is not None:
        d = ((.1, .8, .1, 0) if eg >= 25 else (0, .35, .6, .05) if eg >= 5 else
             (0, 0, .7, .3) if eg >= 0 else (0, 0, .2, .8))
        add(1.5, d, "Crecimiento BPA (trim.)", fmt(eg, "%"))

    e5 = m["eps_next5"]
    if e5 is not None:
        d = ((.1, .85, .05, 0) if e5 >= 20 else (0, .55, .4, .05) if e5 >= 10 else
             (0, .15, .75, .1) if e5 >= 3 else (0, 0, .4, .6))
        add(1.5, d, "BPA próx. 5 años (Finviz)", fmt(e5, "%"))

    pe = m["fpe"] or m["pe"]
    if pe is not None and pe > 0:
        d = (.2, .7, .1, 0) if pe > 35 else (0, .3, .6, .1) if pe >= 15 else (0, 0, .5, .5)
        add(1, d, "P/E (forward o trailing)", fmt(pe, "x"))
    elif m["ps"] is not None and m["ps"] > 8:
        add(1, (.6, .4, 0, 0), "P/S alto sin P/E", fmt(m["ps"], "x"))

    roe = m["roe"]
    if roe is not None:
        d = ((.5, 0, 0, .5) if roe < 0 else (0, .3, .4, .3) if roe < 10 else
             (0, .35, .55, .1) if roe < 20 else (0, .45, .5, .05))
        add(1, d, "ROE", fmt(roe, "%"))

    b = m["beta"]
    if b is not None:
        d = (.2, .6, .2, 0) if b > 1.4 else (0, .35, .55, .1) if b >= 0.9 else (0, 0, .8, .2)
        add(.5, d, "Beta", fmt(b, "", 2))

    dl = m["op_margin_delta"]
    if dl is not None:
        d = (.1, .6, .3, 0) if dl > 2 else (0, .2, .6, .2) if dl >= -2 else (0, 0, .3, .7)
        add(1.5, d, "Cambio margen operativo", f"{dl:+.1f} pp")

    pcts = {p: (tot[p] / W * 100 if W else 0.0) for p in PHASES}
    return pcts, signals


# --------------------------------------------------------------- conclusión
DESC = {
    "Introducción": "ingresos que nacen o crecen desde una base pequeña, rentabilidad y flujo de caja aún débiles o negativos, y un valor que depende de la ejecución y de la financiación.",
    "Crecimiento": "expansión fuerte de ventas con rentabilidad, reinversión de la caja generada, poco o ningún dividendo y una valoración con prima por las expectativas futuras.",
    "Madurez": "crecimiento moderado, márgenes estables, buena generación de caja y retorno al accionista vía dividendos o recompras; el foco pasa a la eficiencia.",
    "Declive": "ventas y utilidades en contracción, márgenes bajo presión y riesgo de 'trampa de valor' aunque los múltiplos parezcan baratos.",
}
WATCH = {
    "Introducción": "la quema de caja, la liquidez disponible, el riesgo de dilución y el camino hacia la rentabilidad.",
    "Crecimiento": "que el crecimiento no se desacelere bruscamente, la sostenibilidad de los márgenes, la competencia y que la valoración no descuente más de lo razonable.",
    "Madurez": "la defensa de cuota de mercado, la disciplina de asignación de capital, la sostenibilidad del dividendo y señales de disrupción.",
    "Declive": "la estabilización de ingresos, la deuda frente a la caja, la capacidad de reinventarse y la posibilidad de desinversiones o reestructuración.",
}


def conclusion(name, tk, pcts, m):
    ranked = sorted(pcts.items(), key=lambda x: -x[1])
    (p1, s1), (p2, s2) = ranked[0], ranked[1]
    g = m["rev_growth"]
    label = p1
    if p1 == "Crecimiento" and g is not None and g >= 40:
        label = "Crecimiento acelerado (hiper-crecimiento)"
    txt = f"**{name} ({tk})** se ubica principalmente en la fase de **{label}** ({s1:.0f}% de las señales)."
    if s1 - s2 < 12:
        txt += f" Muestra rasgos de **transición hacia {p2}** ({s2:.0f}%), así que conviene verla como una empresa en cambio de etapa."
    txt += f"\n\nEsta fase se caracteriza por {DESC[p1]}"
    datos = []
    if g is not None:
        datos.append(f"crecimiento de ingresos de {g:.1f}%")
    if m["net_margin"] is not None:
        datos.append(f"margen neto de {m['net_margin']:.1f}%")
    if m["fcf_margin"] is not None:
        datos.append(f"margen de FCF de {m['fcf_margin']:.1f}%")
    if m["roe"] is not None:
        datos.append(f"ROE de {m['roe']:.1f}%")
    datos.append(f"rendimiento por dividendo de {m['div_yield']:.2f}%")
    txt += "\n\nLos datos que lo respaldan: " + ", ".join(datos) + "."
    txt += f"\n\n**Qué vigilar:** {WATCH[p1]}"
    txt += "\n\n_Análisis cuantitativo basado en reglas con datos de Yahoo Finance y Finviz; es informativo y no constituye una recomendación de inversión._"
    return txt


# ---------------------------------------------------------------------- UI
st.title("📈 Fase del ciclo de vida")
st.caption("Introducción · Crecimiento · Madurez · Declive — con datos de Yahoo Finance y Finviz")

with st.form("form"):
    ticker = st.text_input("Ticker (acción de EE. UU.)", value="NVDA", max_chars=10).strip().upper()
    go = st.form_submit_button("Analizar", use_container_width=True, type="primary")

if go and ticker:
    with st.spinner("Descargando datos de Yahoo Finance y Finviz..."):
        info, inc, cf = get_yahoo(ticker)
        fv = get_finviz(ticker)

    name = info.get("longName") or info.get("shortName")
    if not name and not fv:
        st.error(f"No encontré datos para '{ticker}'. Verifica el ticker.")
        st.stop()
    name = name or ticker

    if not fv:
        st.warning("Finviz no respondió; el análisis usa solo Yahoo Finance (menos señales).")

    m = build_metrics(info, inc, cf, fv)
    pcts, signals = score_phases(m)
    top = max(pcts, key=pcts.get)

    st.header(name)
    st.caption(" · ".join(x for x in [info.get("sector"), info.get("industry")] if x))
    c1, c2 = st.columns(2)
    c1.metric("Precio", fmt(m["price"], " USD", 2))
    mc = num(info.get("marketCap"))
    c2.metric("Market cap", fmt(mc / 1e9 if mc else None, " B USD", 0))
    st.success(f"{EMOJI[top]} Fase dominante: **{top}** ({pcts[top]:.0f}%)")

    st.subheader("Distribución de fases")
    st.bar_chart(pd.DataFrame({"%": pcts}), horizontal=True)

    st.subheader("Ratios clave")
    items = [
        ("Crec. ingresos (trim. a/a)", fmt(m["rev_q_yoy"], "%")),
        ("Crec. ingresos (anual)", fmt(m["rev_a_yoy"], "%")),
        ("CAGR ingresos (años disp.)", fmt(m["rev_cagr"], "%")),
        ("Crec. BPA (trim.)", fmt(m["eps_qq"], "%")),
        ("BPA próx. 5 años", fmt(m["eps_next5"], "%")),
        ("Ventas últ. 5 años", fmt(m["sales_past5"], "%")),
        ("Margen bruto", fmt(m["gross_margin"], "%")),
        ("Margen operativo", fmt(m["op_margin"], "%")),
        ("Margen neto", fmt(m["net_margin"], "%")),
        ("Margen FCF", fmt(m["fcf_margin"], "%")),
        ("ROE", fmt(m["roe"], "%")),
        ("ROA", fmt(m["roa"], "%")),
        ("ROIC / ROI", fmt(m["roi"], "%")),
        ("P/E", fmt(m["pe"], "x")),
        ("P/E forward", fmt(m["fpe"], "x")),
        ("PEG", fmt(m["peg"], "", 2)),
        ("P/S", fmt(m["ps"], "x")),
        ("Deuda/Patrimonio", fmt(m["de"], "", 2)),
        ("Beta", fmt(m["beta"], "", 2)),
        ("Dividendo", f"{m['div_yield']:.2f}%"),
        ("Payout", fmt(m["payout"], "%", 0)),
        ("Rend. 1 año", fmt(m["perf_year"], "%")),
    ]
    for i in range(0, len(items), 2):
        cols = st.columns(2)
        for col, (lab, val) in zip(cols, items[i:i + 2]):
            col.metric(lab, val)

    st.subheader("Señales por indicador")
    st.dataframe(pd.DataFrame(signals), hide_index=True, use_container_width=True)

    rev, ni = m["_rev_s"], m["_ni_s"]
    if rev is not None:
        st.subheader("Histórico anual (miles de millones USD)")
        hist = pd.DataFrame({"Ingresos": rev / 1e9})
        if ni is not None:
            hist["Utilidad neta"] = ni / 1e9
        hist.index = hist.index.year.astype(str)
        st.bar_chart(hist)

    st.subheader("🧭 Conclusión")
    st.info(conclusion(name, ticker, pcts, m))

    st.caption(
        f"Fuentes: [Yahoo Finance](https://finance.yahoo.com/quote/{ticker}/key-statistics) · "
        f"[Finviz](https://finviz.com/quote.ashx?t={ticker})"
    )
