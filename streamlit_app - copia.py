# -*- coding: utf-8 -*-
"""
Generador de Asientos 2.0 — versión integrada con Chatbot (sidebar)
- Carga XLSX/CSV
- Limpieza de filas en blanco
- Reglas de mapeo:
  * GL_Account = 'Código'
  * GL_Month   = 'Mes'
  * GL_Year    = año de 'Fecha'
  * TransactionDate = 'Fecha' formateada DD/MM/AAAA (salida)
  * DebitAmount = 0
  * CreditAmount = 'Venta' (numérico, vacíos->0)
  * JobNumber = 'Trabajo'
  * GL_Group = "" (vacío)
- Igual número de filas de salida que las filas "no vacías" del archivo fuente.
- Descarga en CSV y Excel.
- Chatbot en sidebar (OpenAI u Ollama local).

Requisitos (requirements.txt):
    streamlit>=1.33.0
    pandas>=2.1.0
    openpyxl>=3.1.2
    pyarrow>=15.0.0  # recomendado para CSV/Parquet rápidos
    openai>=1.44.0   # si usas OpenAI
    requests>=2.31.0 # si usas Ollama

Variables de entorno:
    OPENAI_API_KEY="sk-..."  # si usas OpenAI

Ejecución local:
    streamlit run streamlit_app.py
"""
import io
import os
import re
from datetime import datetime
from typing import Tuple

import pandas as pd
import streamlit as st

# =========================
# Config UI
# =========================
st.set_page_config(
    page_title="Generador de Asientos 2.0",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    .stButton > button {background:#16a34a;color:#fff;border:0;border-radius:10px;padding:.6rem 1rem;font-weight:600}
    .stButton > button:hover {filter:brightness(.95)}
    .primary-badge {display:inline-block;background:#f1f5f9;color:#0f172a;padding:.2rem .6rem;border-radius:999px;font-size:.8rem;margin-left:.5rem}
    .ok {color:#16a34a;font-weight:700}
    .warn {color:#ca8a04;font-weight:700}
    .err {color:#dc2626;font-weight:700}
    .small {font-size:.85rem;color:#475569}
    </style>
    """,
    unsafe_allow_html=True
)

# =============================================================================
# Chatbot (sidebar) — OpenAI u Ollama en un solo archivo
# =============================================================================
USE_OPENAI = True          # Cambia a False para usar Ollama local
OPENAI_MODEL = "gpt-4o-mini"
OLLAMA_MODEL = "llama3.1"

def _assistant_reply(messages):
    """Devuelve la respuesta del modelo según backend elegido."""
    try:
        if USE_OPENAI:
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return "⚠️ Falta OPENAI_API_KEY en variables de entorno."
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()
        else:
            import requests, json
            r = requests.post(
                "http://localhost:11434/api/chat",
                headers={"Content-Type":"application/json"},
                data=json.dumps({
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.2}
                })
            )
            r.raise_for_status()
            data = r.json()
            return data.get("message", {}).get("content", "").strip() or "Sin respuesta."
    except Exception as e:
        return f"⚠️ Error al obtener respuesta del modelo: {e}"

def render_chatbot(title="Asistente — Generador de Asientos 2.0", system_prompt=None, state_key="ga2_chat"):
    """Renderiza un chatbot simple en la sidebar."""
    st.sidebar.markdown(f"### 💬 {title}")
    if state_key not in st.session_state:
        st.session_state[state_key] = []

    # Inyectar system_prompt solo una vez
    if system_prompt and not any(m.get("role") == "system" for m in st.session_state[state_key]):
        st.session_state[state_key].insert(0, {"role":"system","content":system_prompt})

    with st.sidebar.expander("Historial", expanded=True):
        for m in st.session_state[state_key]:
            if m["role"] in ("user","assistant"):
                who = "Tú" if m["role"]=="user" else "Asistente"
                st.markdown(f"**{who}:** {m['content']}")

    user_msg = st.sidebar.text_area("Escribe tu mensaje", height=90, key=f"{state_key}_input")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Enviar", use_container_width=True, key=f"{state_key}_send"):
        if user_msg.strip():
            st.session_state[state_key].append({"role":"user","content":user_msg.strip()})
            reply = _assistant_reply(st.session_state[state_key])
            st.session_state[state_key].append({"role":"assistant","content":reply})
            st.sidebar.session_state[f"{state_key}_input"] = ""
            st.rerun()
    if c2.button("Limpiar", use_container_width=True, key=f"{state_key}_clear"):
        msgs = st.session_state[state_key]
        st.session_state[state_key] = [msgs[0]] if msgs and msgs[0].get("role")=="system" else []
        st.sidebar.session_state[f"{state_key}_input"] = ""
        st.rerun()

SYSTEM_PROMPT_GA2 = (
    "Eres un asistente para el 'Generador de Asientos 2.0'. "
    "Objetivo: ayudar a mapear columnas y depurar errores en cargas de Excel/CSV. "
    "Reglas del proyecto: "
    "- GL_Account = 'Código'. "
    "- GL_Month = 'Mes'. "
    "- GL_Year = automático basado en 'Fecha'. "
    "- TransactionDate = 'Fecha' con formato DD/MM/AAAA (salida). "
    "- DebitAmount = 0. "
    "- CreditAmount = 'Venta'. "
    "- JobNumber = 'Trabajo'. "
    "- GL_Group = ''. "
    "- El número de líneas de salida debe igualar el número de filas del Excel original con datos; filas en blanco se ignoran. "
    "Evita errores de conversión (p. ej., could not convert string to float: ''). "
    "Ofrece snippets de pandas cuando ayude."
)
render_chatbot(system_prompt=SYSTEM_PROMPT_GA2)

# =============================================================================
# Utilidades de limpieza y transformación
# =============================================================================
def normalize_column(n: str) -> str:
    """Normaliza nombres de columnas para matching flexible."""
    if not isinstance(n, str):
        return ""
    n2 = n.strip().lower()
    n2 = re.sub(r"[\s_]+", "", n2)
    n2 = n2.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u").replace("ñ","n")
    return n2

def find_col(df: pd.DataFrame, candidates) -> str:
    """Encuentra una columna en df por lista de candidatos (nombres posibles)."""
    norm_map = {normalize_column(c): c for c in df.columns}
    for c in candidates:
        key = normalize_column(c)
        if key in norm_map:
            return norm_map[key]
    # prueba por contiene
    keys = list(norm_map.keys())
    for c in candidates:
        k = normalize_column(c)
        for kk in keys:
            if k in kk:
                return norm_map[kk]
    return ""

def parse_fecha_series(s: pd.Series) -> pd.Series:
    """Convierte una serie a datetime; acepta strings en varios formatos y números de Excel."""
    def _parse_one(x):
        if pd.isna(x):
            return pd.NaT
        if isinstance(x, (datetime, pd.Timestamp)):
            return pd.to_datetime(x)
        # Excel serial date (número)
        if isinstance(x, (int, float)) and x > 20000:
            # pandas to_datetime con 'origin=1899-12-30'
            try:
                return pd.to_datetime(x, unit="D", origin="1899-12-30")
            except Exception:
                pass
        # string
        xs = str(x).strip()
        if not xs:
            return pd.NaT
        # intenta varios formatos
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(xs, fmt)
            except Exception:
                continue
        # fallback: pandas
        try:
            return pd.to_datetime(xs, dayfirst=True, errors="coerce")
        except Exception:
            return pd.NaT
    return s.map(_parse_one)

def to_number_series(s: pd.Series) -> pd.Series:
    """Convierte a número tolerando comas, espacios y vacíos -> 0."""
    def _one(x):
        if pd.isna(x):
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        xs = str(x).strip()
        if xs == "":
            return 0.0
        # remove thousand separators & normalize decimal comma
        xs = xs.replace(" ", "")
        xs = xs.replace(",", ".") if xs.count(",") == 1 and "." not in xs else xs.replace(",", "")
        try:
            return float(xs)
        except Exception:
            return 0.0
    return s.map(_one)

def non_empty_mask(row: pd.Series) -> bool:
    """Define si una fila cuenta como 'con datos' para el conteo."""
    # Consideramos principales columnas de origen
    keys = ["Código", "Mes", "Fecha", "Venta", "Trabajo"]
    for k in keys:
        if k in row and pd.notna(row[k]) and str(row[k]).strip() != "":
            return True
    # si no existen con esos nombres, cualquier valor no vacío en la fila
    return any(pd.notna(v) and str(v).strip() != "" for v in row.values)

def transform(df_in: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """Aplica reglas de transformación y devuelve DF de salida + métricas."""
    # Renombrado flexible: detectar columnas reales
    col_codigo  = find_col(df_in, ["Código", "Codigo", "GL_Account", "Cuenta", "Cod_Cuenta"])
    col_mes     = find_col(df_in, ["Mes", "GL_Month"])
    col_fecha   = find_col(df_in, ["Fecha", "TransactionDate", "F. Transacción", "Fec", "Date"])
    col_venta   = find_col(df_in, ["Venta", "CreditAmount", "Importe", "Monto", "Cr"])
    col_trabajo = find_col(df_in, ["Trabajo", "JobNumber", "Job", "Proyecto"])

    # Copia trabajo
    df = df_in.copy()

    # Conteo de filas con datos (para respetar tamaño de salida)
    mask_data = df.apply(non_empty_mask, axis=1)
    df = df[mask_data].copy()
    in_rows = len(df_in)
    kept_rows = len(df)

    # Parseos
    if col_fecha:
        fechas = parse_fecha_series(df[col_fecha])
        df["__Fecha_dt"] = fechas
    else:
        df["__Fecha_dt"] = pd.NaT

    # Construcción de salida
    out = pd.DataFrame(index=df.index)
    out["GL_Account"]      = df[col_codigo] if col_codigo else ""
    out["GL_Month"]        = df[col_mes] if col_mes else ""
    out["GL_Year"]         = df["__Fecha_dt"].dt.year.fillna(pd.NA)
    out["TransactionDate"] = df["__Fecha_dt"].dt.strftime("%d/%m/%Y")
    out["DebitAmount"]     = 0.0
    out["CreditAmount"]    = to_number_series(df[col_venta]) if col_venta else 0.0
    out["JobNumber"]       = df[col_trabajo] if col_trabajo else ""
    out["GL_Group"]        = ""

    # Validaciones y métricas
    missing = []
    if not col_codigo:  missing.append("Código")
    if not col_mes:     missing.append("Mes")
    if col_fecha == "": missing.append("Fecha")
    if not col_venta:   missing.append("Venta")
    if not col_trabajo: missing.append("Trabajo")

    metrics = {
        "input_rows_total": in_rows,
        "rows_with_data": kept_rows,
        "output_rows": len(out),
        "missing_columns": missing,
        "detected_columns": {
            "Código": col_codigo or "(no encontrada)",
            "Mes": col_mes or "(no encontrada)",
            "Fecha": col_fecha or "(no encontrada)",
            "Venta": col_venta or "(no encontrada)",
            "Trabajo": col_trabajo or "(no encontrada)",
        }
    }
    return out.reset_index(drop=True), metrics

def to_excel_bytes(df: pd.DataFrame, sheet_name="Asientos"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return buf

# =============================================================================
# UI principal
# =============================================================================
st.title("🧾 Generador de Asientos 2.0")
st.caption("Transforma tu Excel/CSV al formato contable requerido — con reglas fijas y consistentes.")

with st.expander("Instrucciones rápidas", expanded=False):
    st.markdown(
        """
        1. Sube tu archivo **Excel (.xlsx)** o **CSV** con columnas como: `Código`, `Mes`, `Fecha`, `Venta`, `Trabajo`.
        2. El sistema limpia filas **completamente en blanco** y respeta la cantidad de filas **con datos**.
        3. Reglas aplicadas:
           - **GL_Account** ← `Código`
           - **GL_Month** ← `Mes`
           - **GL_Year** ← Año de `Fecha`
           - **TransactionDate** ← `Fecha` formateada **DD/MM/AAAA**
           - **DebitAmount** = `0`
           - **CreditAmount** ← `Venta` (vacíos → `0`)
           - **JobNumber** ← `Trabajo`
           - **GL_Group** = `""`
        4. Descarga el resultado en **CSV** o **Excel**.
        """
    )

c_up1, c_up2 = st.columns([2, 1])
with c_up1:
    file = st.file_uploader("Sube tu archivo (.xlsx o .csv)", type=["xlsx", "csv"])
with c_up2:
    sep = st.text_input("Separador CSV (si aplica)", value=",", help="Usado solo si subes CSV.")

if file is not None:
    # Lectura
    try:
        if file.name.lower().endswith(".csv"):
            df_src = pd.read_csv(file, sep=sep, dtype=str, keep_default_na=False, na_values=["", "NA", "NaN"])
            # re-detect types later; keep as strings first to avoid misreads
        else:
            df_src = pd.read_excel(file, dtype=object)
        st.success(f"Archivo cargado: **{file.name}**  ", icon="✅")
    except Exception as e:
        st.error(f"No se pudo leer el archivo: {e}")
        st.stop()

    st.subheader("Vista previa de origen")
    st.dataframe(df_src.head(20), use_container_width=True)
    st.caption(f"Columnas detectadas ({len(df_src.columns)}): {', '.join(map(str, df_src.columns))}")

    # Transformación
    with st.spinner("Procesando reglas..."):
        df_out, metrics = transform(df_src)

    # Métricas
    st.subheader("Resultado")
    c1, c2, c3 = st.columns(3)
    c1.metric("Filas en archivo", metrics["input_rows_total"])
    c2.metric("Filas con datos", metrics["rows_with_data"])
    c3.metric("Filas de salida", metrics["output_rows"])

    det = metrics["detected_columns"]
    st.markdown(
        f"""
        **Columnas mapeadas**  
        - Código → **{det['Código']}**  
        - Mes → **{det['Mes']}**  
        - Fecha → **{det['Fecha']}**  
        - Venta → **{det['Venta']}**  
        - Trabajo → **{det['Trabajo']}**
        """
    )
    if metrics["missing_columns"]:
        st.warning("Columnas no encontradas: " + ", ".join(metrics["missing_columns"]) + ". "
                   "Puedes renombrarlas en el archivo fuente para mejorar el mapeo.")

    # Vista previa salida
    st.dataframe(df_out.head(50), use_container_width=True)

    # Descargas
    colA, colB, colC = st.columns([1,1,2])
    csv_bytes = df_out.to_csv(index=False).encode("utf-8-sig")
    xls_bytes = to_excel_bytes(df_out)

    colA.download_button(
        "⬇️ Descargar CSV",
        data=csv_bytes,
        file_name="asientos_generados.csv",
        mime="text/csv",
        use_container_width=True
    )
    colB.download_button(
        "⬇️ Descargar Excel",
        data=xls_bytes,
        file_name="asientos_generados.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

else:
    st.info("Sube un archivo para comenzar.", icon="📄")

# Pie de página
st.markdown("<hr/>", unsafe_allow_html=True)
st.markdown(
    '<span class="small">© Generador de Asientos 2.0 — versión con chatbot integrado.</span>',
    unsafe_allow_html=True
)
