# -*- coding: utf-8 -*-
import io, re, unicodedata
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

# ---------- Utils ----------
REQUIRED_COLUMNS = ['GL_Account','GL_Month','GL_Year','GL_Group','TransactionDate','DebitAmount','CreditAmount','JobNumber']
DATE_PATTERNS = ['%d/%m/%Y','%m/%d/%Y','%Y-%m-%d','%Y/%m/%d','%d-%m-%Y','%m-%d-%Y','%Y-%m-%d %H:%M:%S','%d/%m/%Y %H:%M:%S','%m/%d/%Y %H:%M:%S']
MONTHS_ES = {1:'Enero',2:'Febrero',3:'Marzo',4:'Abril',5:'Mayo',6:'Junio',7:'Julio',8:'Agosto',9:'Septiembre',10:'Octubre',11:'Noviembre',12:'Diciembre'}

def strip_accents(t):
    if t is None: return ''
    return ''.join(ch for ch in unicodedata.normalize('NFD', str(t)) if not unicodedata.combining(ch))

def parse_date(v):
    """Devuelve MM/DD/YYYY (el TXT usa este formato)."""
    if v is None or str(v).strip()=='':
        raise ValueError("Fecha vacía")
    s = str(v).strip().replace('T',' ').split('.')[0]
    try:
        num = float(s); base = datetime(1899,12,30)
        dt = base + timedelta(days=num)
        return f"{dt.month}/{dt.day}/{dt.year}"
    except Exception:
        pass
    for pat in DATE_PATTERNS:
        try:
            dt = datetime.strptime(s, pat)
            return f"{dt.month}/{dt.day}/{dt.year}"
        except Exception:
            continue
    if len(s)==8 and s.isdigit():
        y,m,d = s[0:4], s[4:6], s[6:8]
        return f"{int(m)}/{int(d)}/{int(y)}"
    raise ValueError(f"No se reconoce formato de fecha: {s}")

def fmt_amount(x):
    s = str(x).strip()
    if s=='' or s.upper()=='NA': val = 0.0
    else:
        s = s.replace(' ','').replace(',','')
        val = float(s)
    return f"{val:.2f}"

def ensure_headers(df):
    df.columns = [str(c).strip() for c in df.columns]
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]

def month_name_es(m):
    m_int = int(float(m))
    if m_int not in MONTHS_ES: raise ValueError(f"GL_Month fuera de 1-12: {m}")
    return MONTHS_ES[m_int], str(m_int)

def normalize_row(row):
    gl_account = str(row['GL_Account']).strip()
    mes_nombre, gl_month = month_name_es(row['GL_Month'])
    gl_year = str(int(float(row['GL_Year']))) if str(row['GL_Year']).strip()!='' else ''
    gl_group = strip_accents(str(row.get('GL_Group','')).strip())
    trx_date = parse_date(row['TransactionDate'])
    debit = fmt_amount(row['DebitAmount'])
    credit = fmt_amount(row['CreditAmount'])
    job = str(row['JobNumber']).strip()
    gl_note = strip_accents(f"Provisión {mes_nombre}")
    gl_reference = strip_accents(f"Provisión producción {mes_nombre} {gl_year}")
    return {'GL_Account': gl_account, 'GL_Note': gl_note, 'GL_Month': gl_month, 'GL_Year': gl_year,
            'GL_Group': gl_group, 'TransactionDate': trx_date, 'GL_Reference': gl_reference,
            'DebitAmount': debit, 'CreditAmount': credit, 'JobNumber': job}

def add_auto_offsets(rows, offset_account='1300102.5', agg='total'):
    base_rows = [normalize_row(r) for r in rows]
    credits = [r for r in base_rows if r['GL_Account'].strip().startswith('8') and float(r['CreditAmount'])>0.0]
    added = []
    if agg == 'none':
        for r in credits:
            added.append({**r, 'GL_Account': offset_account, 'DebitAmount': r['CreditAmount'], 'CreditAmount': '0.00'})
    elif agg in ('total','by_ref','by_job'):
        buckets = defaultdict(list)
        keyfn = (lambda r: 'TOTAL') if agg=='total' else (lambda r: r['GL_Reference'] if agg=='by_ref' else r['JobNumber'])
        for r in credits: buckets[keyfn(r)].append(r)
        for _, group in buckets.items():
            total = sum(float(r['CreditAmount']) for r in group); g0 = group[0]
            if agg == 'total':
                mes_nombre, _ = month_name_es(g0['GL_Month']); anno = g0['GL_Year']
                gl_note = strip_accents(f"Provisión {mes_nombre}")
                gl_ref  = strip_accents(f"Provisión producción {mes_nombre} {anno}")
            else:
                gl_note, gl_ref = g0['GL_Note'], g0['GL_Reference']
            added.append({**g0, 'GL_Account': offset_account, 'GL_Note': gl_note,
                          'GL_Reference': gl_ref, 'DebitAmount': f"{total:.2f}", 'CreditAmount': '0.00'})
    else:
        raise ValueError("agg inválido")
    return base_rows + added

def totals(rows):
    d = sum(float(r['DebitAmount']) for r in rows); c = sum(float(r['CreditAmount']) for r in rows)
    return d, c, round(d-c,2)

def strip_accents_local(s):
    if s is None: return ""
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s)) if not unicodedata.combining(ch))

def normalize_cols(cols):
    out=[]; 
    for c in cols:
        c0=strip_accents_local(str(c)).lower().strip()
        c0=re.sub(r'[^a-z0-9]+','_', c0)
        out.append(c0)
    return out

# ---------- Billing transform ----------
def transform_billing_to_required(df_raw):
    df = df_raw.copy(); df.columns = normalize_cols(df.columns)
    candidates = {
        "codigo": ["codigo","código","gl_account","cuenta","cuenta_contable","account","codigo_cuenta","cta_contable"],
        "mes":    ["mes","gl_month","periodo_mes","periodo","period"],
        "fecha":  ["fecha","transactiondate","fecha_documento","fecha_doc","date","fechafactura","fec_doc","fec_documento"],
        "venta":  ["venta","creditamount","credito","crédito","monto_venta","monto","importe","total","valor","neto"],
        "trabajo":["trabajo","jobnumber","job","proyecto","orden_de_trabajo","ot","orden_trabajo","job_number"]
    }
    def pick(keys): 
        for k in keys:
            if k in df.columns: return k
        return None
    c_codigo = pick(candidates["codigo"]); c_mes = pick(candidates["mes"]); c_fecha = pick(candidates["fecha"])
    c_venta  = pick(candidates["venta"]);  c_trab = pick(candidates["trabajo"])
    out = pd.DataFrame()
    out["GL_Account"] = df[c_codigo] if c_codigo else ""
    if c_fecha:
        fechas_norm = df[c_fecha].apply(lambda v: parse_date(v) if str(v).strip()!="" else "")
        out["TransactionDate"] = fechas_norm
        out["GL_Year"] = fechas_norm.apply(lambda s: datetime.strptime(s,"%m/%d/%Y").year if s else "")
        if c_mes:
            out["GL_Month"] = df[c_mes]
        else:
            out["GL_Month"] = fechas_norm.apply(lambda s: datetime.strptime(s,"%m/%d/%Y").month if s else "")
    else:
        out["TransactionDate"] = ""; out["GL_Year"] = ""; out["GL_Month"] = ""
    out["GL_Group"] = ""; out["DebitAmount"] = 0
    out["CreditAmount"] = (pd.to_numeric(df[c_venta].astype(str).str.replace(" ","").str.replace(",",""), errors="coerce").fillna(0) if c_venta else 0)
    out["JobNumber"] = df[c_trab] if c_trab else ""
    out = out[REQUIRED_COLUMNS].copy()
    def _i(x): sx=str(x).strip(); return "" if sx in ("","nan","None") else int(float(sx))
    out["GL_Month"] = out["GL_Month"].apply(_i); out["GL_Year"] = out["GL_Year"].apply(_i)
    return out

# ---------- AGENCIAS ----------
def _read_agency_df(path: Path):
    try:
        if path.suffix.lower()==".xlsx":
            return pd.read_excel(path, dtype=str)
        return pd.read_csv(path, dtype=str, keep_default_na=False, sep=None, engine="python")
    except Exception:
        return None

def _build_agency_map(df: pd.DataFrame):
    df=df.copy(); df.columns=normalize_cols(df.columns)
    ag_cols=[c for c in df.columns if c in ("agencia","cliente","agente","agency","cliente_nombre")]
    ct_cols=[c for c in df.columns if c in ("cuenta","account","cuenta_contable","num_cuenta")]
    if not ag_cols or not ct_cols: return {}
    a, c = ag_cols[0], ct_cols[0]
    m={}
    for _,r in df.iterrows():
        ag=str(r[a]).strip(); cu=str(r[c]).strip()
        if ag and cu and cu.lower()!="nan": m[ag]=cu
    return m

def load_agencies():
    base = Path(__file__).parent
    paths = [base/"agencias.csv", base/"agencias.xlsx", base/"data"/"agencias.csv", base/"data"/"agencias.xlsx", base/"assets"/"agencias.csv", base/"assets"/"agencias.xlsx"]
    for p in paths:
        if p.exists():
            df = _read_agency_df(p)
            if df is not None:
                m = _build_agency_map(df)
                if m: return m
    return {}

agency_map = load_agencies()

# ---------- UI de agencias ----------
selected_agency = None; offset_account_from_agency=None
use_agency_account=False; agency_error=False

if agency_map:
    cols = st.columns([2,2,2])
    with cols[0]:
        selected_agency = st.selectbox("Agencia", sorted(agency_map.keys()))
    with cols[1]:
        use_agency_account = st.checkbox("Usar cuenta según agencia", value=True)
    if use_agency_account and selected_agency:
        acct = agency_map.get(selected_agency, "").strip()
        if not acct:
            st.error(f"La agencia **{selected_agency}** no tiene cuenta en la base.")
            agency_error=True
        else:
            offset_account_from_agency = acct
            with cols[2]:
                st.text_input("Cuenta (auto por agencia)", value=acct, disabled=True)
else:
    st.info("ℹ️ Coloca **agencias.csv** (o .xlsx) en la raíz del repo con columnas: Agencia, Cuenta.")

# ---------- Input principal ----------
file = st.file_uploader("Sube tu archivo Billing original (CSV o Excel .xlsx)", type=['csv','xlsx'])
run = st.button("▶ Ejecutar y generar salida.txt", disabled=agency_error)

with st.expander("Opciones avanzadas (contrapartida y texto)", expanded=False):
    agg = st.selectbox("Tipo de contrapartida", options=['total','none','by_ref','by_job'], index=0)
    manual_help = "Se usará la cuenta por agencia si está activado arriba." if use_agency_account else "Se usará esta cuenta."
    offset_account_manual = st.text_input("Cuenta de contrapartida (manual)", value="1300102.5", help=manual_help)

st.markdown("---")

# ---------- Ejecutar ----------
if run:
    if not file:
        st.error("Sube un archivo primero."); st.stop()
    try:
        if file.name.lower().endswith('.csv'):
            raw = file.getvalue(); df_in = None
            for enc in ('utf-8-sig','cp1252','latin1'):
                try:
                    text = raw.decode(enc, errors='strict')
                    df_in = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine='python'); break
                except Exception: continue
            if df_in is None:
                st.error("No pude leer el CSV. Guarda como 'CSV UTF-8' o sube un .xlsx."); st.stop()
        else:
            df_in = pd.read_excel(file, dtype=str)

        st.subheader("Vista previa - Billing original"); st.dataframe(df_in.head(20))

        missing = ensure_headers(df_in.copy())
        df_req = transform_billing_to_required(df_in) if missing else df_in.copy()

        st.subheader("Vista previa - Billing_formateado (intermedio)"); st.dataframe(df_req.head(20))

        rows = df_req.to_dict(orient='records')
        effective_offset = offset_account_from_agency if (use_agency_account and offset_account_from_agency) else offset_account_manual
        out_rows = add_auto_offsets(rows, offset_account=effective_offset, agg=agg)

        d,c,diff = totals(out_rows)

        out_buffer = io.StringIO()
        for r in out_rows:
            fields = [r['GL_Account'], r['GL_Note'], r['GL_Month'], r['GL_Year'], r['GL_Group'],
                      r['TransactionDate'], r['GL_Reference'], r['DebitAmount'], r['CreditAmount'], r['JobNumber']]
            out_buffer.write('\t'.join(fields) + '\n')
        data = out_buffer.getvalue().encode('utf-8')

        st.success("Archivo generado.")
        st.write(f"**Débitos:** {d:.2f}  |  **Créditos:** {c:.2f}  |  **Diferencia:** {diff:.2f}")
        st.download_button("⬇ Descargar salida.txt", data=data, file_name="salida.txt", mime="text/plain")

    except Exception as e:
        st.exception(e)
