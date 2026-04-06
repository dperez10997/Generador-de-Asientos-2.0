# -*- coding: utf-8 -*-
import io, re, unicodedata, math, calendar
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import pandas as pd
import streamlit as st

# ---------- Config UI ----------
st.set_page_config(page_title="Generador de Asientos Producción", page_icon="🧾", layout="wide")
st.markdown(
    "<style>.stButton > button{background:#16a34a;color:#fff;border:0;border-radius:10px;padding:.8rem 1.2rem;font-weight:600} .stButton > button:hover{filter:brightness(.95)}</style>",
    unsafe_allow_html=True
)
st.title("Generador de Asientos Producción")
st.caption("Sube tu Excel/CSV (Billing original). La app lo transforma al formato requerido y genera el TXT tabulado.")

# ---------- Constantes / Utils ----------
REQUIRED_COLUMNS = [
    'GL_Account','GL_Month','GL_Year','GL_Group',
    'TransactionDate','DebitAmount','CreditAmount','JobNumber'
]
MONTHS_ES = {
    1:'Enero',2:'Febrero',3:'Marzo',4:'Abril',5:'Mayo',6:'Junio',
    7:'Julio',8:'Agosto',9:'Septiembre',10:'Octubre',11:'Noviembre',12:'Diciembre'
}

def strip_accents(s):
    if s is None:
        return ''
    return ''.join(ch for ch in unicodedata.normalize('NFD', str(s)) if not unicodedata.combining(ch))

def _blank(v) -> bool:
    s = str(v).strip().lower()
    return s in ("", "nan", "none", "nat", "null")

def parse_date_any(v, month=None, year=None) -> str:
    """
    Devuelve MM/DD/YYYY o "" si no puede.
    - Acepta NaN/None
    - Acepta seriales de Excel
    - Usa pandas.to_datetime con dayfirst False/True
    - Si no hay fecha pero hay mes/año -> último día del mes
    """
    if v is None or _blank(v):
        if month and year:
            try:
                m = int(float(month))
                y = int(float(year))
                last = calendar.monthrange(y, m)[1]
                return f"{m}/{last}/{y}"
            except Exception:
                return ""
        return ""

    s = str(v).strip()

    # 1) Serial Excel
    try:
        num = float(s)
        if not math.isnan(num) and 1 <= num <= 80000:
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=num)
            return f"{dt.month}/{dt.day}/{dt.year}"
    except Exception:
        pass

    # 2) pandas.to_datetime (infer + dayfirst)
    for day_first in (False, True):
        try:
            dt = pd.to_datetime(s, dayfirst=day_first, errors="coerce", utc=False)
            if pd.notna(dt):
                if isinstance(dt, pd.Timestamp):
                    d = dt.to_pydatetime()
                else:
                    d = dt[0].to_pydatetime()
                return f"{d.month}/{d.day}/{d.year}"
        except Exception:
            continue

    # 3) YYYYMMDD
    if len(s) == 8 and s.isdigit():
        try:
            y, m, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
            return f"{m}/{d}/{y}"
        except Exception:
            pass

    return ""  # nunca lanzar excepción

def fmt_amount(x):
    s = str(x).strip()
    if s == '' or s.upper() == 'NA':
        val = 0.0
    else:
        s = s.replace(' ', '').replace(',', '')
        val = float(s)
    return f"{val:.2f}"

def ensure_headers(df):
    df.columns = [str(c).strip() for c in df.columns]
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]

def month_name_es(m, trx_date=None):
    """
    Devuelve (nombre_mes, mes_str). Si m viene vacío/invalid,
    intenta inferirlo desde trx_date (cualquier formato soportado).
    Si no logra inferir, devuelve ("","") sin lanzar excepción.
    """
    # 1) intento directo
    try:
        m_int = int(float(str(m).strip()))
        if 1 <= m_int <= 12:
            return MONTHS_ES[m_int], str(m_int)
    except Exception:
        pass

    # 2) inferir desde fecha
    s = parse_date_any(trx_date) if trx_date is not None else ""
    if s:
        try:
            dt = datetime.strptime(s, "%m/%d/%Y")
            m_int = dt.month
            return MONTHS_ES[m_int], str(m_int)
        except Exception:
            pass

    # 3) no se pudo
    return "", ""

def make_unique_text(base: str, job: str, seq: int) -> str:
    base = strip_accents(str(base).strip()) if base is not None else ""
    job = strip_accents(str(job).strip()) if job is not None else ""
    if job:
        return f"{base} - {job} - {seq:03d}"
    return f"{base} - {seq:03d}"


def normalize_row(row):
    gl_account = str(row.get('GL_Account', '')).strip()

    # Mes (infiere desde fecha si viene vacío)
    trx_date_raw = row.get('TransactionDate')
    mes_nombre, gl_month = month_name_es(row.get('GL_Month'), trx_date_raw)

    # Año (intenta directo; si no, inferir desde fecha)
    gl_year_val = str(row.get('GL_Year', '')).strip()
    if gl_year_val == "":
        s = parse_date_any(trx_date_raw, month=gl_month, year=None)
        if s:
            try:
                gl_year_val = str(datetime.strptime(s, "%m/%d/%Y").year)
            except Exception:
                gl_year_val = ""
    else:
        try:
            gl_year_val = str(int(float(gl_year_val)))
        except Exception:
            s = parse_date_any(trx_date_raw, month=gl_month, year=None)
            if s:
                try:
                    gl_year_val = str(datetime.strptime(s, "%m/%d/%Y").year)
                except Exception:
                    gl_year_val = ""

    gl_group = strip_accents(str(row.get('GL_Group', '')).strip())
    trx_date = parse_date_any(trx_date_raw, month=gl_month, year=gl_year_val if gl_year_val else None)
    debit = fmt_amount(row.get('DebitAmount', 0))
    credit = fmt_amount(row.get('CreditAmount', 0))
    job = str(row.get('JobNumber', '')).strip()
    gl_note = strip_accents(f"Provisión {mes_nombre}") if mes_nombre else "Provision"
    gl_reference = strip_accents(
        f"Provisión producción {mes_nombre} {gl_year_val}".strip()
    ) if mes_nombre or gl_year_val else "Provision produccion"

    return {
        'GL_Account': gl_account, 'GL_Note': gl_note,
        'GL_Month': gl_month, 'GL_Year': gl_year_val, 'GL_Group': gl_group,
        'TransactionDate': trx_date, 'GL_Reference': gl_reference,
        'DebitAmount': debit, 'CreditAmount': credit, 'JobNumber': job
    }


def add_auto_offsets(rows, offset_account='1300102.5', agg='total'):
    base_rows = [normalize_row(r) for r in rows]

    seq_by_key = defaultdict(int)
    for r in base_rows:
        note_key = (r['GL_Account'], r['GL_Note'], r['GL_Month'], r['GL_Year'], r['TransactionDate'])
        seq_by_key[note_key] += 1
        seq = seq_by_key[note_key]
        r['GL_Note'] = make_unique_text(r['GL_Note'], r['JobNumber'], seq)
        r['GL_Reference'] = make_unique_text(r['GL_Reference'], r['JobNumber'], seq)

    credits = [r for r in base_rows if r['GL_Account'].strip().startswith('8') and float(r['CreditAmount']) > 0.0]
    added = []
    if agg == 'none':
        for i, r in enumerate(credits, start=1):
            debit_row = {**r, 'GL_Account': offset_account, 'DebitAmount': r['CreditAmount'], 'CreditAmount': '0.00'}
            debit_row['GL_Note'] = make_unique_text(r['GL_Note'], r['JobNumber'], i)
            debit_row['GL_Reference'] = make_unique_text(r['GL_Reference'], r['JobNumber'], i)
            added.append(debit_row)
    elif agg in ('total', 'by_ref', 'by_job'):
        buckets = defaultdict(list)
        keyfn = (lambda r: 'TOTAL') if agg == 'total' else (lambda r: r['GL_Reference'] if agg == 'by_ref' else r['JobNumber'])
        for r in credits:
            buckets[keyfn(r)].append(r)
        for idx, (_, group) in enumerate(buckets.items(), start=1):
            total = sum(float(r['CreditAmount']) for r in group)
            g0 = group[0]
            if agg == 'total':
                mes_nombre, _ = month_name_es(g0['GL_Month'], g0['TransactionDate'])
                anno = g0['GL_Year']
                gl_note = strip_accents(f"Provisión {mes_nombre}") if mes_nombre else "Provision"
                gl_ref = strip_accents(f"Provisión producción {mes_nombre} {anno}".strip()) if mes_nombre or anno else "Provision produccion"
            else:
                gl_note, gl_ref = g0['GL_Note'], g0['GL_Reference']
            added.append({
                **g0,
                'GL_Account': offset_account,
                'GL_Note': make_unique_text(gl_note, g0['JobNumber'], idx),
                'GL_Reference': make_unique_text(gl_ref, g0['JobNumber'], idx),
                'DebitAmount': f"{total:.2f}",
                'CreditAmount': '0.00'
            })
    else:
        raise ValueError("agg inválido")
    return base_rows + added


def totals(rows):
    d = sum(float(r['DebitAmount']) for r in rows)
    c = sum(float(r['CreditAmount']) for r in rows)
    return d, c, round(d - c, 2)

def strip_accents_local(s):
    if s is None:
        return ""
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s)) if not unicodedata.combining(ch))

def normalize_cols(cols):
    out = []
    for c in cols:
        c0 = strip_accents_local(str(c)).lower().strip()
        c0 = re.sub(r'[^a-z0-9]+', '_', c0)
        out.append(c0)
    return out

# ---------- Filtros de filas vacías ----------
def _cell_blank(x) -> bool:
    if x is None:
        return True
    s = str(x).strip().lower()
    return s in ("", "nan", "none", "null")

def drop_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Quita filas completamente vacías del Billing original."""
    if df.empty:
        return df
    mask_all_blank = df.apply(lambda col: col.map(_cell_blank)).all(axis=1)
    return df.loc[~mask_all_blank].reset_index(drop=True)

def drop_empty_transformed(df: pd.DataFrame) -> pd.DataFrame:
    """Quita filas vacías o sin monto del formateado (REQUIRED_COLUMNS)."""
    if df.empty:
        return df

    def _zero_or_blank_amount(x):
        s = str(x).strip()
        if _cell_blank(s):
            return True
        try:
            return float(s) == 0.0
        except Exception:
            return True

    mask_empty = (
        df["GL_Account"].apply(_cell_blank)
        & df["TransactionDate"].apply(_cell_blank)
        & df["JobNumber"].apply(_cell_blank)
        & df["CreditAmount"].apply(_zero_or_blank_amount)
        & df["DebitAmount"].apply(_zero_or_blank_amount)
    )
    return df.loc[~mask_empty].reset_index(drop=True)

# ---------- Transformación Billing -> requerido ----------
def transform_billing_to_required(df_raw):
    df = df_raw.copy()
    df.columns = normalize_cols(df.columns)
    candidates = {
        "codigo": ["codigo", "código", "gl_account", "cuenta", "cuenta_contable", "account", "codigo_cuenta", "cta_contable"],
        "mes": ["mes", "gl_month", "periodo_mes", "periodo", "period"],
        "fecha": ["fecha", "transactiondate", "fecha_documento", "fecha_doc", "date", "fechafactura", "fec_doc", "fec_documento"],
        "venta": ["venta", "creditamount", "credito", "crédito", "monto_venta", "monto", "importe", "total", "valor", "neto"],
        "trabajo": ["trabajo", "jobnumber", "job", "proyecto", "orden_de_trabajo", "ot", "orden_trabajo", "job_number"]
    }

    def pick(keys):
        for k in keys:
            if k in df.columns:
                return k
        return None

    c_codigo = pick(candidates["codigo"])
    c_mes = pick(candidates["mes"])
    c_fecha = pick(candidates["fecha"])
    c_venta = pick(candidates["venta"])
    c_trab = pick(candidates["trabajo"])

    out = pd.DataFrame()
    out["GL_Account"] = df[c_codigo] if c_codigo else ""

    # Fecha normalizada (tolerante)
    if c_fecha:
        fechas_norm = df[c_fecha].apply(lambda v: parse_date_any(v))
        out["TransactionDate"] = fechas_norm

        def _year(s):
            try:
                dt = datetime.strptime(s, "%m/%d/%Y")
                return dt.year
            except Exception:
                return ""

        def _month(s):
            try:
                dt = datetime.strptime(s, "%m/%d/%Y")
                return dt.month
            except Exception:
                return ""

        out["GL_Year"] = fechas_norm.apply(_year)
        if c_mes:
            out["GL_Month"] = df[c_mes]
        else:
            out["GL_Month"] = fechas_norm.apply(_month)
    else:
        out["TransactionDate"] = ""
        out["GL_Year"] = ""
        out["GL_Month"] = ""

    out["GL_Group"] = ""
    out["DebitAmount"] = 0
    out["CreditAmount"] = (
        pd.to_numeric(
            (df[c_venta] if c_venta else 0).astype(str).str.replace(" ", "", regex=False).str.replace(",", "", regex=False),
            errors="coerce"
        ).fillna(0) if c_venta else 0
    )
    out["JobNumber"] = df[c_trab] if c_trab else ""

    out = out[REQUIRED_COLUMNS].copy()

    def _int_or_blank(x):
        sx = str(x).strip().lower()
        if sx in ("", "nan", "none", "null"):
            return ""
        return int(float(sx))

    out["GL_Month"] = out["GL_Month"].apply(_int_or_blank)
    out["GL_Year"] = out["GL_Year"].apply(_int_or_blank)

    # Si TransactionDate está vacío pero hay Mes/Año -> fabricar último día del mes
    def _fill_date(row):
        if str(row["TransactionDate"]).strip() == "" and row["GL_Month"] != "" and row["GL_Year"] != "":
            return parse_date_any("", month=row["GL_Month"], year=row["GL_Year"])
        return row["TransactionDate"]

    out["TransactionDate"] = out.apply(_fill_date, axis=1)

    return out

# ---------- AGENCIAS ----------
def _read_agency_df(path: Path):
    try:
        if path.suffix.lower() == ".xlsx":
            return pd.read_excel(path, dtype=str)
        # CSV con autodetección de separador y encodings comunes
        for enc in ("utf-8-sig", "cp1252", "latin1"):
            try:
                return pd.read_csv(path, dtype=str, keep_default_na=False, sep=None, engine="python", encoding=enc)
            except Exception:
                continue
        return None
    except Exception:
        return None

def _build_agency_map(df: pd.DataFrame):
    df = df.copy()
    df.columns = normalize_cols(df.columns)
    ag_cols = [c for c in df.columns if c in ("agencia", "cliente", "agente", "agency", "cliente_nombre")]
    ct_cols = [c for c in df.columns if c in ("cuenta", "account", "cuenta_contable", "num_cuenta")]
    if not ag_cols or not ct_cols:
        return {}
    a, c = ag_cols[0], ct_cols[0]
    m = {}
    for _, r in df.iterrows():
        ag = str(r[a]).strip()
        cu = str(r[c]).strip()
        if ag and cu and cu.lower() != "nan":
            m[ag] = cu
    return m

def load_agencies():
    base = Path(__file__).parent
    paths = [
        base / "agencias.csv", base / "agencias.xlsx",
        base / "data" / "agencias.csv", base / "data" / "agencias.xlsx",
        base / "assets" / "agencias.csv", base / "assets" / "agencias.xlsx"
    ]
    for p in paths:
        if p.exists():
            df = _read_agency_df(p)
            if df is not None:
                m = _build_agency_map(df)
                if m:
                    return m
    return {}

agency_map = load_agencies()

# ---------- UI de agencias ----------
selected_agency = None
offset_account_from_agency = None
use_agency_account = False
agency_error = False

if agency_map:
    cols = st.columns([2, 2, 2])
    with cols[0]:
        selected_agency = st.selectbox("Agencia", sorted(agency_map.keys()))
    with cols[1]:
        use_agency_account = st.checkbox("Usar cuenta según agencia", value=True)
    if use_agency_account and selected_agency:
        acct = agency_map.get(selected_agency, "").strip()
        if not acct:
            st.error(f"La agencia **{selected_agency}** no tiene cuenta en la base.")
            agency_error = True
        else:
            offset_account_from_agency = acct
            with cols[2]:
                st.text_input("Cuenta (auto por agencia)", value=acct, disabled=True)
else:
    st.info("ℹ️ Coloca **agencias.csv** (o .xlsx) en la raíz del repo con columnas: Agencia, Cuenta.")

# ---------- Input principal ----------
file = st.file_uploader("Sube tu archivo Billing original (CSV o Excel .xlsx)", type=['csv', 'xlsx'])
run = st.button("▶ Ejecutar y generar salida.txt", disabled=agency_error)

with st.expander("Opciones avanzadas (contrapartida y texto)", expanded=False):
    agg = st.selectbox("Tipo de contrapartida", options=['total', 'none', 'by_ref', 'by_job'], index=0)
    manual_help = "Se usará la cuenta por agencia si está activado arriba." if use_agency_account else "Se usará esta cuenta."
    offset_account_manual = st.text_input("Cuenta de contrapartida (manual)", value="1300102.5", help=manual_help)

st.markdown("---")

# ---------- Ejecutar ----------
if run:
    if not file:
        st.error("Sube un archivo primero.")
        st.stop()
    try:
        # Leer Billing original
        if file.name.lower().endswith('.csv'):
            raw = file.getvalue()
            df_in = None
            for enc in ('utf-8-sig', 'cp1252', 'latin1'):
                try:
                    text = raw.decode(enc, errors='strict')
                    df_in = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine='python')
                    break
                except Exception:
                    continue
            if df_in is None:
                st.error("No pude leer el CSV. Guarda como 'CSV UTF-8' o sube un .xlsx.")
                st.stop()
        else:
            df_in = pd.read_excel(file, dtype=str)

        # Eliminar filas completamente vacías del Billing original
        df_in = drop_empty_rows(df_in)

        st.subheader("Vista previa - Billing original")
        st.dataframe(df_in.head(20))

        # Transformar si hace falta
        missing = ensure_headers(df_in.copy())
        df_req = transform_billing_to_required(df_in) if missing else df_in.copy()

        # Eliminar filas vacías/sin monto del formateado
        df_req = drop_empty_transformed(df_req)

        st.subheader("Vista previa - Billing_formateado (intermedio)")
        st.dataframe(df_req.head(20))

        rows = df_req.to_dict(orient='records')
        effective_offset = offset_account_from_agency if (use_agency_account and offset_account_from_agency) else offset_account_manual
        out_rows = add_auto_offsets(rows, offset_account=effective_offset, agg=agg)

        d, c, diff = totals(out_rows)

        # Construir TXT
        out_buffer = io.StringIO()
        for r in out_rows:
            fields = [
                r['GL_Account'], r['GL_Note'], r['GL_Month'], r['GL_Year'], r['GL_Group'],
                r['TransactionDate'], r['GL_Reference'], r['DebitAmount'], r['CreditAmount'], r['JobNumber']
            ]
            out_buffer.write('\t'.join(fields) + '\n')
        data = out_buffer.getvalue().encode('utf-8')

        st.success("Archivo generado.")
        st.write(f"**Débitos:** {d:.2f}  |  **Créditos:** {c:.2f}  |  **Diferencia:** {diff:.2f}")
        st.download_button("⬇ Descargar salida.txt", data=data, file_name="salida.txt", mime="text/plain")

    except Exception as e:
        st.exception(e)
