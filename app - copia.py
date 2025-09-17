# -*- coding: utf-8 -*-
import io, os, re, unicodedata
from datetime import datetime, timedelta
from collections import defaultdict
import pandas as pd
import streamlit as st

# ---------- Config UI ----------
st.set_page_config(
    page_title="Generador de Asientos Producción",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown(
    "<style>.stButton > button{background:#16a34a;color:#fff;border:0;border-radius:10px;padding:.8rem 1.2rem;font-weight:600} .stButton > button:hover{filter:brightness(.95)}</style>",
    unsafe_allow_html=True
)
st.title("Generador de Asientos Producción")
st.caption("Sube tu Excel/CSV (Billing original). La app lo transforma al formato requerido y genera el TXT tabulado.")

# ---------- Utils ----------
REQUIRED_COLUMNS = [
    'GL_Account','GL_Month','GL_Year','GL_Group',
    'TransactionDate','DebitAmount','CreditAmount','JobNumber'
]
DATE_PATTERNS = [
    '%d/%m/%Y','%m/%d/%Y','%Y-%m-%d','%Y/%m/%d',
    '%d-%m-%Y','%m-%d-%Y','%Y-%m-%d %H:%M:%S','%d/%m/%Y %H:%M:%S','%m/%d/%Y %H:%M:%S'
]
MONTHS_ES = {
    1:'Enero',2:'Febrero',3:'Marzo',4:'Abril',5:'Mayo',6:'Junio',
    7:'Julio',8:'Agosto',9:'Septiembre',10:'Octubre',11:'Noviembre',12:'Diciembre'
}

def strip_accents(t):
    if t is None: return ''
    return ''.join(ch for ch in unicodedata.normalize('NFD', str(t)) if not unicodedata.combining(ch))

def parse_date(v):
    """Normaliza fechas a MM/DD/YYYY (la salida.txt usa este formato)."""
    if v is None or str(v).strip()=='':
        raise ValueError("Fecha vacía")
    s = str(v).strip().replace('T',' ').split('.')[0]
    # Excel serial
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
    if s=='' or s.upper()=='NA':
        val = 0.0
    else:
        s = s.replace(' ','').replace(',','')
        val = float(s)
    return f"{val:.2f}"

def ensure_headers(df):
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    return missing

def month_name_es(m):
    try:
        m_int = int(float(m))
    except Exception:
        raise ValueError(f"GL_Month inválido: {m}")
    if m_int not in MONTHS_ES:
        raise ValueError(f"GL_Month fuera de rango (1-12): {m}")
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
    return {
        'GL_Account': gl_account, 'GL_Note': gl_note,
        'GL_Month': gl_month, 'GL_Year': gl_year, 'GL_Group': gl_group,
        'TransactionDate': trx_date, 'GL_Reference': gl_reference,
        'DebitAmount': debit, 'CreditAmount': credit, 'JobNumber': job
    }

def add_auto_offsets(rows, offset_account='1300102.5', agg='total',
                     offset_note="Provision (auto)", offset_ref="Provision produccion (auto)"):
    """
    Genera contrapartidas:
      - Si agg == 'total': una contrapartida por el total (Nota/Referencia auto por mes/año del archivo).
      - Si agg == 'none': espejo por cada crédito 8xxxx.
      - Si agg == 'by_ref' / 'by_job': agrupa por referencia o JobNumber.
    """
    base_rows = [normalize_row(r) for r in rows]
    credits = [r for r in base_rows if r['GL_Account'].strip().startswith('8') and float(r['CreditAmount'])>0.0]
    added = []

    if agg == 'none':
        for r in credits:
            added.append({
                **r, 'GL_Account': offset_account,
                'DebitAmount': r['CreditAmount'], 'CreditAmount': '0.00'
            })

    elif agg in ('total','by_ref','by_job'):
        buckets = defaultdict(list)
        if agg == 'total':
            keyfn = lambda r: 'TOTAL'
        elif agg == 'by_ref':
            keyfn = lambda r: r['GL_Reference']
        else:  # by_job
            keyfn = lambda r: r['JobNumber']

        for r in credits:
            buckets[keyfn(r)].append(r)

        for _, group in buckets.items():
            total = sum(float(r['CreditAmount']) for r in group)
            g0 = group[0]

            if agg == 'total':
                mes_nombre, _ = month_name_es(g0['GL_Month'])
                anno = g0['GL_Year']
                gl_note = strip_accents(f"Provisión {mes_nombre}")
                gl_ref  = strip_accents(f"Provisión producción {mes_nombre} {anno}")
            else:
                gl_note = g0['GL_Note']
                gl_ref  = g0['GL_Reference']

            added.append({
                **g0,
                'GL_Account': offset_account,
                'GL_Note': gl_note,
                'GL_Reference': gl_ref,
                'DebitAmount': f"{total:.2f}",
                'CreditAmount': '0.00'
            })
    else:
        raise ValueError("Valor inválido para 'agg'")

    return base_rows + added

def totals(rows):
    d = sum(float(r['DebitAmount']) for r in rows)
    c = sum(float(r['CreditAmount']) for r in rows)
    return d, c, round(d-c,2)

# ---------- Lectores auxiliares ----------
def read_any_csv(file_bytes):
    for enc in ('utf-8-sig','cp1252','latin1'):
        try:
            text = file_bytes.decode(enc, errors='strict')
            return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine='python')
        except Exception:
            continue
    return None

def strip_accents_local(s):
    if s is None: return ""
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s)) if not unicodedata.combining(ch))

def normalize_cols(cols):
    out=[]
    for c in cols:
        c0=strip_accents_local(str(c)).lower().strip()
        c0=re.sub(r'[^a-z0-9]+','_', c0)
        out.append(c0)
    return out

# ---------- NUEVO: Transformador de Billing original -> columnas requeridas ----------
def transform_billing_to_required(df_raw):
    """
    Acepta el Billing original (con columnas como Código, Mes, Fecha, Venta, Trabajo)
    y devuelve un DataFrame con las columnas REQUIRED_COLUMNS completas.
    """
    df = df_raw.copy()
    df.columns = normalize_cols(df.columns)

    # Mapeo de sinónimos normalizados
    candidates = {
        "codigo": ["codigo","código","gl_account","cuenta","cuenta_contable","account","codigo_cuenta","cta_contable"],
        "mes":    ["mes","gl_month","periodo_mes","periodo","period"],
        "fecha":  ["fecha","transactiondate","fecha_documento","fecha_doc","date","fechafactura","fec_doc","fec_documento"],
        "venta":  ["venta","creditamount","credito","crédito","monto_venta","monto","importe","total","valor","neto"],
        "trabajo":["trabajo","jobnumber","job","proyecto","orden_de_trabajo","ot","orden_trabajo","job_number"]
    }

    def pick(col_aliases):
        for a in col_aliases:
            if a in df.columns:
                return a
        return None

    col_codigo = pick(candidates["codigo"])
    col_mes    = pick(candidates["mes"])
    col_fecha  = pick(candidates["fecha"])
    col_venta  = pick(candidates["venta"])
    col_trab   = pick(candidates["trabajo"])

    out = pd.DataFrame()

    # GL_Account
    out["GL_Account"] = df[col_codigo] if col_codigo else ""

    # TransactionDate y derivados
    if col_fecha:
        # normalizamos a texto MM/DD/YYYY para calcular Year y Month si hace falta
        fechas_norm = df[col_fecha].apply(lambda v: parse_date(v) if str(v).strip()!="" else "")
        out["TransactionDate"] = fechas_norm
        # Año
        def year_from_str(s):
            try:
                dt = datetime.strptime(s, "%m/%d/%Y")
                return dt.year
            except Exception:
                return ""
        out["GL_Year"] = fechas_norm.apply(year_from_str)
        # Mes: si no viene explícito, lo tomamos de la fecha
        if col_mes:
            out["GL_Month"] = df[col_mes]
        else:
            def month_from_str(s):
                try:
                    dt = datetime.strptime(s, "%m/%d/%Y")
                    return dt.month
                except Exception:
                    return ""
            out["GL_Month"] = fechas_norm.apply(month_from_str)
    else:
        # Sin fecha no podemos derivar Year/Mes
        out["TransactionDate"] = ""
        out["GL_Year"] = ""
        out["GL_Month"] = ""

    # GL_Group vacío
    out["GL_Group"] = ""

    # DebitAmount = 0
    out["DebitAmount"] = 0

    # CreditAmount
    if col_venta:
        out["CreditAmount"] = pd.to_numeric(df[col_venta].astype(str).str.replace(" ","").str.replace(",",""), errors="coerce").fillna(0)
    else:
        out["CreditAmount"] = 0

    # JobNumber
    out["JobNumber"] = df[col_trab] if col_trab else ""

    # Orden y tipos
    out = out[REQUIRED_COLUMNS].copy()
    # Limpieza final de tipos
    out["GL_Month"] = out["GL_Month"].apply(lambda x: int(float(x)) if str(x).strip() not in ("","nan","None") else "")
    out["GL_Year"]  = out["GL_Year"].apply(lambda x: int(float(x)) if str(x).strip() not in ("","nan","None") else "")
    # Formateo de fecha en salida a DD/MM/AAAA si quieres ver en tabla; el TXT seguirá usando MM/DD/YYYY al normalizar
    # (lo deja parse_date durante normalize_row)
    return out

# ---------- Agencia -> Cuenta (base fija en repo) ----------
def build_agency_map(df):
    df=df.copy(); df.columns=normalize_cols(df.columns)
    ag_cols=[c for c in df.columns if 'agencia' in c or 'cliente' in c or c in ('agencia','agente')]
    ct_cols=[c for c in df.columns if 'cuenta' in c or 'account' in c]
    if not ag_cols or not ct_cols: return {}
    agencia_col=ag_cols[0]; cuenta_col=ct_cols[0]
    mapping={}
    for _, r in df.iterrows():
        ag=str(r[agencia_col]).strip(); ct=str(r[cuenta_col]).strip()
        if ag and ct and ct.lower()!='nan': mapping[ag]=ct
    return mapping

def load_fixed_agency_map():
    for fname in ("agencias.xlsx","agencias.csv"):
        if os.path.isfile(fname):
            try:
                if fname.endswith(".xlsx"):
                    df=pd.read_excel(fname, dtype=str, engine='openpyxl')
                else:
                    with open(fname,"rb") as f: df=read_any_csv(f.read())
                m=build_agency_map(df)
                if m: return m
            except Exception: pass
    return {}

fixed_agency_map = load_fixed_agency_map()

# ---------- Selector de AGENCIA visible (arriba) ----------
selected_agency=None
offset_account_from_agency=None
use_agency_account=False
agency_error=False

if fixed_agency_map:
    cols = st.columns([2,2,2])
    with cols[0]:
        selected_agency = st.selectbox("Agencia", sorted(fixed_agency_map.keys()))
    with cols[1]:
        use_agency_account = st.checkbox("Usar cuenta según agencia", value=True)

    if use_agency_account and selected_agency:
        if selected_agency not in fixed_agency_map or str(fixed_agency_map.get(selected_agency, "")).strip()=="":
            st.error(f"La agencia **{selected_agency}** no tiene una cuenta asignada en la base. Usa la **cuenta manual** en Opciones avanzadas.")
            use_agency_account = False
            agency_error = True
        else:
            offset_account_from_agency = fixed_agency_map[selected_agency]
            with cols[2]:
                st.text_input("Cuenta (auto por agencia)", value=offset_account_from_agency, disabled=True)

# ---------- Uploader y botón ----------
file = st.file_uploader("Sube tu archivo Billing original (CSV o Excel .xlsx)", type=['csv','xlsx'])
run = st.button("▶ Ejecutar y generar salida.txt", disabled=agency_error)

# ---------- Opciones avanzadas ----------
with st.expander("Opciones avanzadas (contrapartida y texto)", expanded=False):
    agg = st.selectbox("Tipo de contrapartida", options=['total','none','by_ref','by_job'], index=0)
    auto_offset = st.checkbox("Generar contrapartida automática", value=True)
    manual_help = "Se usará la cuenta por agencia si está activado arriba." if use_agency_account else "Se usará esta cuenta."
    offset_account_manual = st.text_input("Cuenta de contrapartida (manual)", value="1300102.5", help=manual_help)
    st.text_input("Nota (si 'total')", value="(Se autogenera: Provisión <Mes>)", disabled=True)
    st.text_input("Referencia (si 'total')", value="(Se autogenera: Provisión producción <Mes> <Año>)", disabled=True)

st.markdown("---")
st.caption("Puedes subir tu Billing original con columnas como Código, Mes, Fecha, Venta, Trabajo. La app lo transforma al formato mínimo requerido antes de generar el TXT.")

# ---------- Ejecutar ----------
if run:
    if not file:
        st.error("Sube un archivo primero."); st.stop()
    try:
        # 1) Leer Billing original
        if file.name.lower().endswith('.csv'):
            raw = file.getvalue()
            df_in = None
            for enc in ('utf-8-sig','cp1252','latin1'):
                try:
                    text = raw.decode(enc, errors='strict')
                    df_in = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine='python')
                    break
                except Exception:
                    continue
            if df_in is None:
                st.error("No pude leer el CSV. Guarda como 'CSV UTF-8' o sube un .xlsx."); st.stop()
        else:
            df_in = pd.read_excel(file, dtype=str, engine='openpyxl')

        st.subheader("Vista previa - Billing original")
        st.dataframe(df_in.head(20))

        # 2) Intentar detectar si YA viene en formato requerido
        missing = ensure_headers(df_in.copy())
        if missing:
            # 3) Transformar Billing original -> requerido
            df_req = transform_billing_to_required(df_in)
            missing_after = ensure_headers(df_req.copy())
            if missing_after:
                st.error("No pude construir todas las columnas requeridas. Revisa que el archivo tenga al menos: Código, Fecha y Venta (y opcionalmente Mes y Trabajo).")
                st.stop()
        else:
            df_req = df_in.copy()

        # Preview del formateado
        st.subheader("Vista previa - Billing_formateado (intermedio)")
        st.dataframe(df_req.head(20))

        # 4) Pipeline existente (normalización + contrapartidas + TXT)
        rows = df_req.to_dict(orient='records')

        # Cuenta de contrapartida efectiva
        effective_offset = offset_account_from_agency if (use_agency_account and offset_account_from_agency) else offset_account_manual

        if auto_offset:
            out_rows = add_auto_offsets(
                rows,
                offset_account=effective_offset,
                agg=agg
            )
        else:
            out_rows = [normalize_row(r) for r in rows]

        d, c, diff = totals(out_rows)

        # Construir TXT (tabulado)
        out_buffer = io.StringIO()
        for r in out_rows:
            fields = [r['GL_Account'], r['GL_Note'], r['GL_Month'], r['GL_Year'], r['GL_Group'],
                      r['TransactionDate'], r['GL_Reference'], r['DebitAmount'], r['CreditAmount'], r['JobNumber']]
            out_buffer.write('\t'.join(fields) + '\n')
        data = out_buffer.getvalue().encode('utf-8')

        st.success("Archivo generado.")
        st.write(f"**Débitos:** {d:.2f}  |  **Créditos:** {c:.2f}  |  **Diferencia (D-C):** {diff:.2f}")
        if abs(diff)<=0.01: st.write("✅ Asiento **CUADRA**.")
        else: st.warning("⚠️  Asiento **NO cuadra**. Revisa montos/agrupación.")

        st.download_button("⬇ Descargar salida.txt", data=data, file_name="salida.txt", mime="text/plain")

        # (Opcional) también puedes permitir descargar el Billing formateado intermedio:
        out_xlsx = io.BytesIO()
        df_req.to_excel(out_xlsx, index=False)
        st.download_button("⬇ Descargar Billing_formateado.xlsx", data=out_xlsx.getvalue(),
                           file_name="Billing_formateado.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    except Exception as e:
        st.exception(e)
