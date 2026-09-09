import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from calendar import monthrange
from streamlit_gsheets import GSheetsConnection

# =============================================================
# CONFIGURACIÓN GENERAL
# =============================================================
META_ESPANA = 522800

# Nombres EXACTOS de las pestañas dentro de tu archivo de Google Sheets
HOJA_MOVIMIENTOS = "registro_financiero"
HOJA_PRESUPUESTOS = "presupuestos"
HOJA_COBROS = "cobros"

COLS_MOV = ["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion"]
COLS_PRE = ["ID", "Categoria", "Detalle", "Monto", "Frecuencia", "DiaPago", "Inicio", "Expiracion"]
COLS_COB = ["ID", "Concepto", "Monto", "Frecuencia", "DiaCobro", "Inicio", "Expiracion"]

CATEGORIAS_GASTO = [
    "Ocio", "Entretenimiento", "Servicios basicos", "Mandado",
    "Gasolina", "Universidad", "Prestamos o deudas", "Casa",
]

FRECUENCIAS = ["Mensual", "Semanal", "Catorcenal", "Unica"]

DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}

MESES_NOMBRES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

ABREV_DIA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

st.set_page_config(page_title="Proyecto España 2028", page_icon="🇪🇸", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)


# =============================================================
# LECTURA / ESCRITURA GENÉRICA
# =============================================================
def esqueleto(columnas):
    vacio = pd.DataFrame(columns=columnas)
    if "Monto" in columnas:
        vacio["Monto"] = vacio["Monto"].astype(float)
    return vacio


@st.cache_data(ttl=300, show_spinner=False)
def leer_hoja(nombre, columnas):
    datos = conn.read(worksheet=nombre)
    if datos is None or datos.empty:
        return esqueleto(columnas)
    datos = datos.dropna(how="all")
    datos = datos.loc[:, ~datos.columns.astype(str).str.startswith("Unnamed")]
    for col in columnas:
        if col not in datos.columns:
            datos[col] = None
    return datos[columnas].reset_index(drop=True)


def guardar_hoja(nombre, datos, columnas):
    try:
        limpio = datos.copy()
        for col in columnas:
            if col not in limpio.columns:
                limpio[col] = None
        limpio = limpio[columnas].reset_index(drop=True)
        for col in ["Inicio", "Expiracion", "Fecha"]:
            if col in limpio.columns:
                fechas = pd.to_datetime(limpio[col], errors="coerce")
                limpio[col] = fechas.dt.strftime("%Y-%m-%d").fillna("")
        limpio = limpio.fillna("")
        conn.update(worksheet=nombre, data=limpio)
        st.cache_data.clear()
        return True, f"✅ Guardado en la hoja «{nombre}»."
    except Exception as e:
        return False, f"⚠️ Error al guardar en «{nombre}»: {e}"


def a_numero(serie):
    limpio = (
        serie.astype(str)
        .str.replace(r"[^0-9\.\-]", "", regex=True)
        .replace("", None)
    )
    return pd.to_numeric(limpio, errors="coerce").fillna(0.0)


def texto_dia(serie):
    """Los días vienen como '15,30' o 'Sabado'; Sheets a veces los devuelve como 15.0."""
    return (
        serie.fillna("").astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
        .replace({"nan": "", "None": ""})
    )


def nuevo_id(offset=0):
    return str(int(pd.Timestamp.now().timestamp() * 1000) + offset)


def rellenar_ids(tabla):
    ids = []
    for n, valor in enumerate(tabla["ID"]):
        texto = str(valor).strip()
        ids.append(texto if texto not in ("", "nan", "None") else nuevo_id(n))
    tabla = tabla.copy()
    tabla["ID"] = ids
    return tabla


# =============================================================
# MOVIMIENTOS
# =============================================================
def normalizar_movimientos(datos):
    if datos is None or datos.empty:
        return esqueleto(COLS_MOV)

    datos = datos.copy()
    datos["Monto"] = a_numero(datos["Monto"])

    fecha_dt = pd.to_datetime(datos["Fecha"], errors="coerce")
    datos = datos[fecha_dt.notna()].copy()
    fecha_dt = fecha_dt[fecha_dt.notna()]
    if datos.empty:
        return esqueleto(COLS_MOV)

    datos["Fecha"] = fecha_dt.dt.strftime("%Y-%m-%d")
    datos["Fecha_DT"] = fecha_dt
    datos["Anio"] = fecha_dt.dt.year.astype(int)
    datos["Mes"] = fecha_dt.dt.month.astype(int)
    datos["Periodo_Label"] = fecha_dt.dt.strftime("%Y - %m")

    for col in ["Tipo", "Categoria", "Descripcion"]:
        datos[col] = datos[col].fillna("").astype(str).str.strip()

    datos["ID"] = datos["ID"].astype(str).replace({"nan": "", "None": ""})
    faltantes = datos["ID"] == ""
    if faltantes.any():
        base = int(pd.Timestamp.now().timestamp() * 1000)
        datos.loc[faltantes, "ID"] = [str(base + i) for i in range(int(faltantes.sum()))]

    return datos.reset_index(drop=True)


def solo_columnas_mov(datos):
    limpio = datos.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore").copy()
    for col in COLS_MOV:
        if col not in limpio.columns:
            limpio[col] = None
    return limpio[COLS_MOV].reset_index(drop=True)


# =============================================================
# MOTOR DE CALENDARIO
# =============================================================
def _a_fecha(valor):
    f = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(f) else f.date()


def ocurrencias(frecuencia, dia_txt, inicio, expiracion, desde, hasta):
    """Fechas en que aplica una regla dentro del rango [desde, hasta]."""
    freq = str(frecuencia or "Mensual").strip().lower()
    partes = [p.strip() for p in str(dia_txt or "").replace(";", ",").split(",") if p.strip()]
    inicio = _a_fecha(inicio)
    expiracion = _a_fecha(expiracion)
    fechas = []

    if freq.startswith("men"):
        cursor = date(desde.year, desde.month, 1)
        limite = date(hasta.year, hasta.month, 1)
        while cursor <= limite:
            ultimo = monthrange(cursor.year, cursor.month)[1]
            for p in partes:
                clave = p.lower()
                if clave in ("fin", "ultimo", "último", "fin de mes"):
                    dia = ultimo
                else:
                    try:
                        dia = int(float(p))
                    except ValueError:
                        continue
                    dia = min(max(dia, 1), ultimo)
                fechas.append(date(cursor.year, cursor.month, dia))
            cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)

    elif freq.startswith("sem"):
        objetivos = {DIAS_SEMANA[p.lower()] for p in partes if p.lower() in DIAS_SEMANA}
        cursor = desde
        while cursor <= hasta:
            if cursor.weekday() in objetivos:
                fechas.append(cursor)
            cursor += timedelta(days=1)

    elif freq.startswith("cat") or freq.startswith("quin"):
        if inicio:
            cursor = inicio
            while cursor < desde:
                cursor += timedelta(days=14)
            while cursor <= hasta:
                fechas.append(cursor)
                cursor += timedelta(days=14)

    elif freq.startswith("uni"):
        for p in partes:
            f = _a_fecha(p)
            if f:
                fechas.append(f)
        if not partes and inicio:
            fechas.append(inicio)

    resultado = []
    for f in sorted(set(fechas)):
        if f < desde or f > hasta:
            continue
        if inicio and f < inicio:
            continue
        if expiracion and f > expiracion:
            continue
        resultado.append(f)
    return resultado


def eventos_en_rango(df_pre, df_cob, desde, hasta):
    """Une pagos programados y cobros en una sola línea de tiempo ordenada."""
    eventos = []

    for _, row in df_pre.iterrows():
        if str(row.get("Detalle") or "").strip() == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        for f in ocurrencias(row.get("Frecuencia"), row.get("DiaPago"), row.get("Inicio"),
                             row.get("Expiracion"), desde, hasta):
            eventos.append({
                "Fecha": f,
                "Tipo": "Pago",
                "Concepto": f"{row.get('Categoria', '')} — {row.get('Detalle', '')}".strip(" —"),
                "Monto": monto,
            })

    for _, row in df_cob.iterrows():
        if str(row.get("Concepto") or "").strip() == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        for f in ocurrencias(row.get("Frecuencia"), row.get("DiaCobro"), row.get("Inicio"),
                             row.get("Expiracion"), desde, hasta):
            eventos.append({
                "Fecha": f,
                "Tipo": "Cobro",
                "Concepto": str(row.get("Concepto") or ""),
                "Monto": monto,
            })

    if not eventos:
        return pd.DataFrame(columns=["Fecha", "Tipo", "Concepto", "Monto"])

    tabla = pd.DataFrame(eventos)
    # Si un cobro y un pago caen el mismo día, primero entra el dinero
    tabla["_orden"] = tabla["Tipo"].map({"Cobro": 0, "Pago": 1})
    return tabla.sort_values(["Fecha", "_orden", "Concepto"]).drop(columns="_orden").reset_index(drop=True)


def equivalente_mensual(df_pre, referencia=None):
    """Cuánto suma cada categoría dentro del mes calendario de referencia."""
    referencia = referencia or date.today()
    inicio_mes = date(referencia.year, referencia.month, 1)
    fin_mes = date(referencia.year, referencia.month, monthrange(referencia.year, referencia.month)[1])

    activos, expirados = {}, []
    for _, row in df_pre.iterrows():
        cat = str(row.get("Categoria") or "").strip()
        if cat == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        exp = _a_fecha(row.get("Expiracion"))
        if exp and exp < inicio_mes:
            expirados.append(f"{cat} ({row.get('Detalle', '')})")
            continue
        veces = len(ocurrencias(row.get("Frecuencia"), row.get("DiaPago"), row.get("Inicio"),
                                row.get("Expiracion"), inicio_mes, fin_mes))
        activos[cat] = activos.get(cat, 0.0) + monto * veces

    return {k: v for k, v in activos.items() if v > 0}, expirados


# =============================================================
# CARGA DE DATOS
# =============================================================
st.title("🇪🇸 Tablero Financiero: Proyecto España 2028")

errores = []
try:
    df = normalizar_movimientos(leer_hoja(HOJA_MOVIMIENTOS, COLS_MOV))
except Exception as e:
    errores.append((HOJA_MOVIMIENTOS, e))
    df = esqueleto(COLS_MOV)

try:
    df_pre = leer_hoja(HOJA_PRESUPUESTOS, COLS_PRE)
    df_pre["Monto"] = a_numero(df_pre["Monto"])
    df_pre["DiaPago"] = texto_dia(df_pre["DiaPago"])
except Exception as e:
    errores.append((HOJA_PRESUPUESTOS, e))
    df_pre = esqueleto(COLS_PRE)

try:
    df_cob = leer_hoja(HOJA_COBROS, COLS_COB)
    df_cob["Monto"] = a_numero(df_cob["Monto"])
    df_cob["DiaCobro"] = texto_dia(df_cob["DiaCobro"])
except Exception as e:
    errores.append((HOJA_COBROS, e))
    df_cob = esqueleto(COLS_COB)

for nombre, err in errores:
    st.error(
        f"No se pudo leer la pestaña **{nombre}**. Verifica que exista con ese nombre exacto y "
        f"que el archivo esté compartido como Editor con la cuenta de servicio.\n\n`{err}`"
    )

st.session_state.setdefault("modo_revision", False)
st.session_state.setdefault("ignorar_alerta_cuadre", False)

presupuestos_activos, expirados = equivalente_mensual(df_pre)


# =============================================================
# FILTROS
# =============================================================
st.subheader("🔍 Selector de Periodos y Acumulados")

if not df.empty:
    periodos = sorted(df["Periodo_Label"].dropna().unique(), reverse=True)
    etiqueta_actual = pd.Timestamp.now().strftime("%Y - %m")
    default = [etiqueta_actual] if etiqueta_actual in periodos else periodos[:1]

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        meses_seleccionados = st.multiselect(
            "Meses a sumar (acumulado flexible):",
            options=periodos,
            default=default,
            format_func=lambda p: f"{p.split(' - ')[0]} - {MESES_NOMBRES[int(p.split(' - ')[1])]}",
        )
    with col_f2:
        anios = sorted(df["Anio"].dropna().unique().tolist(), reverse=True)
        anio_seleccionado = st.selectbox("Año para el resumen anual:", options=anios, index=0)

    df_filtrado = df[df["Periodo_Label"].isin(meses_seleccionados)]
else:
    st.info("Aún no hay movimientos. Registra el primero desde la barra lateral.")
    df_filtrado = esqueleto(COLS_MOV)
    anio_seleccionado = date.today().year

st.divider()


def suma_por_tipo(datos, tipo):
    if datos.empty or "Tipo" not in datos.columns:
        return 0.0
    return float(datos.loc[datos["Tipo"] == tipo, "Monto"].sum())


total_ingresos_f = suma_por_tipo(df_filtrado, "Ingreso")
total_gastos_f = suma_por_tipo(df_filtrado, "Gasto")
total_ahorro_f = suma_por_tipo(df_filtrado, "Ahorro")
suma_gastos_ahorro = total_gastos_f + total_ahorro_f
margen_disponible = total_ingresos_f - suma_gastos_ahorro

alerta_cuadre_activa = total_ingresos_f > 0 and abs(suma_gastos_ahorro - total_ingresos_f) > 1.0


# =============================================================
# BARRA LATERAL
# =============================================================
if st.session_state.modo_revision:
    st.sidebar.warning("🔒 **Registro bloqueado**\n\nEstás en modo de revisión del historial.")
    if st.sidebar.button("Salir del modo revisión", use_container_width=True):
        st.session_state.modo_revision = False
        st.rerun()
else:
    st.sidebar.header("📝 Registrar Movimiento")
    fecha = st.sidebar.date_input("Fecha", date.today())
    tipo = st.sidebar.selectbox("Tipo de movimiento", ["Gasto", "Ingreso", "Ahorro"], key="tipo_mov")

    if tipo == "Gasto":
        categoria = st.sidebar.selectbox("Categoría", CATEGORIAS_GASTO, key="cat_gasto")
    elif tipo == "Ahorro":
        categoria = st.sidebar.selectbox("Categoría", ["Fondo España"], key="cat_ahorro")
    else:
        categoria = st.sidebar.selectbox("Categoría", ["Sueldo Fijo", "Trabajos Extra"], key="cat_ingreso")

    monto = st.sidebar.number_input("Monto ($)", min_value=0.0, step=100.0, key="monto_mov")
    descripcion = st.sidebar.text_input("Descripción", key="desc_mov")

    if st.sidebar.button("Guardar Movimiento", type="primary", use_container_width=True):
        if not (2024 <= fecha.year <= 2035):
            st.sidebar.error("⚠️ El año está fuera del rango válido.")
        elif monto <= 0:
            st.sidebar.error("⚠️ El monto debe ser mayor a 0.")
        else:
            nuevo = pd.DataFrame([{
                "ID": nuevo_id(),
                "Fecha": fecha.strftime("%Y-%m-%d"),
                "Anio": int(fecha.year),
                "Mes": int(fecha.month),
                "Tipo": tipo,
                "Categoria": categoria,
                "Monto": float(monto),
                "Descripcion": descripcion,
            }])
            exito, mensaje = guardar_hoja(
                HOJA_MOVIMIENTOS,
                pd.concat([solo_columnas_mov(df), nuevo], ignore_index=True),
                COLS_MOV,
            )
            if exito:
                st.session_state.modo_revision = False
                st.session_state.ignorar_alerta_cuadre = False
                st.sidebar.success(mensaje)
                st.rerun()
            else:
                st.sidebar.error(mensaje)

st.sidebar.divider()
st.sidebar.caption(f"Movimientos: {len(df)} · Partidas: {len(df_pre)} · Cobros: {len(df_cob)}")


# =============================================================
# ALERTA DE CUADRE
# =============================================================
if alerta_cuadre_activa and not st.session_state.ignorar_alerta_cuadre:
    st.error(
        "🚨 **Desajuste financiero en el periodo**\n\n"
        f"Ingresos (**${total_ingresos_f:,.2f}**) vs gastos + ahorros (**${suma_gastos_ahorro:,.2f}**). "
        f"Diferencia: **${margen_disponible:,.2f}**."
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("📝 Faltan movimientos por registrar", type="primary", use_container_width=True):
            st.session_state.ignorar_alerta_cuadre = True
            st.rerun()
    with c2:
        if st.button("💡 Faltan gastos por hacer", use_container_width=True):
            st.session_state.ignorar_alerta_cuadre = True
            st.rerun()
    with c3:
        if st.button("🔍 Revisar el historial", use_container_width=True):
            st.session_state.modo_revision = True
            st.rerun()
    st.divider()


# =============================================================
# PESTAÑAS
# =============================================================
tab_mes, tab_anual, tab_pre, tab_semaforo, tab_admin = st.tabs(
    ["📊 Resumen de Periodo", "📅 Resumen del Año", "💰 Presupuestos y Calendario",
     "🚦 Semáforo 30 días", "⚙️ Administrar Historial"]
)

with tab_mes:
    st.header("Resumen del periodo seleccionado")

    tasa_ahorro = (total_ahorro_f / total_ingresos_f * 100) if total_ingresos_f > 0 else 0.0
    fondo_espana = float(df.loc[df["Categoria"] == "Fondo España", "Monto"].sum()) if not df.empty else 0.0
    faltante_meta = max(META_ESPANA - fondo_espana, 0.0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingresos totales", f"${total_ingresos_f:,.2f}")
    c2.metric("Gastos totales", f"${total_gastos_f:,.2f}")
    c3.metric("Margen disponible", f"${margen_disponible:,.2f}", delta=f"{tasa_ahorro:.1f}% tasa de ahorro")
    c4.metric("Fondo España", f"${fondo_espana:,.2f}", delta=f"Faltan ${faltante_meta:,.2f}")

    avance = 0.0 if META_ESPANA <= 0 else min(max(fondo_espana / META_ESPANA, 0.0), 1.0)
    st.progress(avance, text=f"Avance hacia la meta: {avance * 100:.1f}%")

    if not df_filtrado.empty:
        for cat, lim in presupuestos_activos.items():
            gasto = float(df_filtrado.loc[
                (df_filtrado["Tipo"] == "Gasto") & (df_filtrado["Categoria"] == cat), "Monto"].sum())
            if lim > 0 and gasto > lim:
                st.warning(
                    f"⚠️ **{cat}**: llevas ${gasto:,.2f} y tu límite mensual activo es ${lim:,.2f} "
                    f"(excedente de ${gasto - lim:,.2f})."
                )

    st.markdown("---")
    st.subheader("📈 Gráficas del periodo")
    if df_filtrado.empty:
        st.info("Selecciona un periodo con movimientos.")
    else:
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Gastos por categoría**")
            df_gastos = df_filtrado[df_filtrado["Tipo"] == "Gasto"]
            if df_gastos.empty:
                st.info("Sin gastos en este periodo.")
            else:
                st.bar_chart(df_gastos.groupby("Categoria", as_index=False)["Monto"].sum(),
                             x="Categoria", y="Monto")
        with g2:
            st.markdown("**Ingresos vs gastos vs ahorro**")
            st.bar_chart(pd.DataFrame(
                {"Monto": [total_ingresos_f, total_gastos_f, total_ahorro_f]},
                index=["Ingresos", "Gastos", "Ahorro España"],
            ))

with tab_anual:
    st.header(f"📅 Resumen anual: {anio_seleccionado}")
    df_anual = df[df["Anio"] == anio_seleccionado] if not df.empty else esqueleto(COLS_MOV)

    ingresos_anio = suma_por_tipo(df_anual, "Ingreso")
    gastos_anio = suma_por_tipo(df_anual, "Gasto")
    ahorro_anio = suma_por_tipo(df_anual, "Ahorro")
    tasa_anio = (ahorro_anio / ingresos_anio * 100) if ingresos_anio > 0 else 0.0
    fondo_total = float(df.loc[df["Categoria"] == "Fondo España", "Monto"].sum()) if not df.empty else 0.0

    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Ingresos anuales", f"${ingresos_anio:,.2f}")
    a2.metric("Gastos anuales", f"${gastos_anio:,.2f}")
    a3.metric("Ahorro anual", f"${ahorro_anio:,.2f}")
    a4.metric("Tasa de ahorro", f"{tasa_anio:.1f}%")
    a5.metric("Faltante meta", f"${max(META_ESPANA - fondo_total, 0.0):,.2f}")

    st.markdown("---")
    if df_anual.empty:
        st.info(f"Sin movimientos en {anio_seleccionado}.")
    else:
        modo = st.radio("Visualización:", ["Mes a mes (detalle)", "Acumulado del año"], horizontal=True)
        tabla = df_anual.groupby(["Mes", "Tipo"])["Monto"].sum().unstack(fill_value=0.0).reset_index()
        for col in ["Ingreso", "Gasto", "Ahorro"]:
            if col not in tabla.columns:
                tabla[col] = 0.0
        tabla = pd.DataFrame({"Mes": range(1, 13)}).merge(tabla, on="Mes", how="left").fillna(0.0)
        tabla["Nombre Mes"] = tabla["Mes"].map(MESES_NOMBRES)
        tabla = tabla.sort_values("Mes")

        if modo == "Mes a mes (detalle)":
            st.dataframe(
                tabla[["Nombre Mes", "Ingreso", "Gasto", "Ahorro"]].rename(
                    columns={"Nombre Mes": "Mes", "Ingreso": "Ingresos ($)",
                             "Gasto": "Gastos ($)", "Ahorro": "Ahorro ($)"}),
                use_container_width=True, hide_index=True,
            )
            st.line_chart(tabla.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]])
        else:
            acum = tabla.copy()
            acum[["Ingreso", "Gasto", "Ahorro"]] = acum[["Ingreso", "Gasto", "Ahorro"]].cumsum()
            st.line_chart(acum.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]])

with tab_pre:
    st.header("💰 Presupuestos y calendario")
    st.caption(
        "Frecuencias · **Mensual**: día(s) del mes, ej. `15,30` o `Fin`. "
        "**Semanal**: nombre del día, ej. `Sabado` o `Lunes,Viernes`. "
        "**Catorcenal**: cada 14 días contando desde *Inicio*. "
        "**Unica**: una fecha `AAAA-MM-DD` en la columna del día."
    )

    st.subheader("💵 Días de cobro")
    cobros_edit = st.data_editor(
        df_cob,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.TextColumn("ID", disabled=True, help="Se genera solo al guardar"),
            "Concepto": st.column_config.TextColumn("Concepto", required=True),
            "Monto": st.column_config.NumberColumn("Monto ($)", min_value=0.0, format="$%.2f", step=100.0),
            "Frecuencia": st.column_config.SelectboxColumn("Frecuencia", options=FRECUENCIAS, required=True),
            "DiaCobro": st.column_config.TextColumn("Día(s) de cobro"),
            "Inicio": st.column_config.TextColumn("Inicio (AAAA-MM-DD)"),
            "Expiracion": st.column_config.TextColumn("Expira (AAAA-MM-DD)"),
        },
        key="editor_cobros",
    )
    if st.button("💾 Guardar días de cobro", type="primary"):
        limpio = cobros_edit[cobros_edit["Concepto"].astype(str).str.strip() != ""].copy()
        exito, mensaje = guardar_hoja(HOJA_COBROS, rellenar_ids(limpio), COLS_COB)
        if exito:
            st.success(mensaje)
            st.rerun()
        else:
            st.error(mensaje)

    st.divider()
    st.subheader("📂 Partidas de gasto por categoría")
    cat_sel = st.selectbox("Categoría a revisar o editar:", CATEGORIAS_GASTO)
    df_cat = df_pre[df_pre["Categoria"] == cat_sel].reset_index(drop=True)

    pre_edit = st.data_editor(
        df_cat[["ID", "Detalle", "Monto", "Frecuencia", "DiaPago", "Inicio", "Expiracion"]],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.TextColumn("ID", disabled=True, help="Se genera solo al guardar"),
            "Detalle": st.column_config.TextColumn("Concepto", required=True),
            "Monto": st.column_config.NumberColumn("Monto ($)", min_value=0.0, format="$%.2f", step=100.0),
            "Frecuencia": st.column_config.SelectboxColumn("Frecuencia", options=FRECUENCIAS, required=True),
            "DiaPago": st.column_config.TextColumn("Día(s) de pago"),
            "Inicio": st.column_config.TextColumn("Inicio (AAAA-MM-DD)"),
            "Expiracion": st.column_config.TextColumn("Expira (AAAA-MM-DD)"),
        },
        key=f"editor_pre_{cat_sel}",
    )

    if st.button(f"💾 Guardar partidas de {cat_sel}", type="primary"):
        resto = df_pre[df_pre["Categoria"] != cat_sel].copy()
        nuevas = pre_edit[pre_edit["Detalle"].astype(str).str.strip() != ""].copy()
        if not nuevas.empty:
            nuevas["Categoria"] = cat_sel
            nuevas = rellenar_ids(nuevas)
        exito, mensaje = guardar_hoja(
            HOJA_PRESUPUESTOS, pd.concat([resto, nuevas], ignore_index=True), COLS_PRE)
        if exito:
            st.success(mensaje)
            st.rerun()
        else:
            st.error(mensaje)

    st.divider()
    st.subheader("📋 Equivalente mensual activo")
    if presupuestos_activos:
        st.dataframe(
            pd.DataFrame([
                {"Categoría": c, "Total del mes": f"${v:,.2f}", "Apartado semanal (÷4)": f"${v / 4:,.2f}"}
                for c, v in sorted(presupuestos_activos.items())
            ]),
            use_container_width=True, hide_index=True,
        )
        total_mensual = sum(presupuestos_activos.values())
        t1, t2 = st.columns(2)
        t1.metric("💰 Presupuesto mensual global", f"${total_mensual:,.2f}")
        t2.metric("📅 Total semanal a apartar", f"${total_mensual / 4:,.2f}")
    else:
        st.info("No hay partidas activas configuradas para este mes.")

    if expirados:
        st.success(f"✅ Ya expiraron y no se cuentan: **{', '.join(sorted(set(expirados)))}**")

with tab_semaforo:
    st.header("🚦 Semáforo de los próximos días")

    s1, s2 = st.columns([1, 2])
    with s1:
        dias_vista = st.slider("Días a proyectar", 7, 90, 30)
    with s2:
        saldo_inicial = st.number_input(
            "Saldo disponible hoy ($)",
            value=float(round(margen_disponible, 2)),
            step=100.0,
            help="Por defecto usa tu margen del periodo (Ingresos − Gastos − Ahorro). "
                 "Ajústalo si el dinero real en tu cuenta es otro.",
        )

    hoy = date.today()
    linea = eventos_en_rango(df_pre, df_cob, hoy, hoy + timedelta(days=dias_vista))

    if linea.empty:
        st.info("No hay cobros ni pagos programados en este rango. Configúralos en «Presupuestos y Calendario».")
    else:
        saldo = float(saldo_inicial)
        filas, faltante_total, primer_problema = [], 0.0, None

        for _, ev in linea.iterrows():
            if ev["Tipo"] == "Cobro":
                saldo += ev["Monto"]
                estado = "💵 ENTRA DINERO"
                detalle = f"Saldo tras el cobro: ${saldo:,.2f}"
            else:
                if saldo >= ev["Monto"]:
                    saldo -= ev["Monto"]
                    estado = "✅ ALCANZA"
                    detalle = f"Te quedan ${saldo:,.2f}"
                else:
                    faltan = ev["Monto"] - max(saldo, 0.0)
                    faltante_total += faltan
                    if primer_problema is None:
                        primer_problema = (ev["Fecha"], ev["Concepto"], ev["Monto"], max(saldo, 0.0), faltan)
                    saldo -= ev["Monto"]
                    estado = "🔴 NO ALCANZA"
                    detalle = f"Faltan ${faltan:,.2f} · saldo quedaría en ${saldo:,.2f}"

            filas.append({
                "Fecha": ev["Fecha"].strftime("%d/%m"),
                "Día": ABREV_DIA[ev["Fecha"].weekday()],
                "Movimiento": ev["Concepto"],
                "Monto": ("+" if ev["Tipo"] == "Cobro" else "−") + f"${ev['Monto']:,.2f}",
                "Estado": estado,
                "Detalle": detalle,
            })

        if primer_problema:
            f, concepto, monto_p, saldo_p, faltan_p = primer_problema
            st.error(
                f"⚠️ **Peligro el {f.strftime('%d/%m/%Y')}**: tienes «{concepto}» por **${monto_p:,.2f}** "
                f"y tu saldo proyectado para esa fecha es de **${saldo_p:,.2f}**. "
                f"Faltan **${faltan_p:,.2f}**."
            )
            if faltante_total > faltan_p + 0.01:
                st.warning(f"Faltante acumulado en todo el rango: **${faltante_total:,.2f}**.")
        else:
            st.success(f"✅ Todos los pagos de los próximos {dias_vista} días están cubiertos.")

        m1, m2, m3 = st.columns(3)
        m1.metric("Saldo proyectado al final", f"${saldo:,.2f}")
        m2.metric("Total a pagar", f"${linea.loc[linea['Tipo'] == 'Pago', 'Monto'].sum():,.2f}")
        m3.metric("Total a cobrar", f"${linea.loc[linea['Tipo'] == 'Cobro', 'Monto'].sum():,.2f}")

        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

with tab_admin:
    st.header("⚙️ Administrar historial de movimientos")
    if df.empty:
        st.info("No hay movimientos que administrar.")
    else:
        df_admin = st.data_editor(
            solo_columnas_mov(df),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "ID": st.column_config.TextColumn("ID", disabled=True),
                "Fecha": st.column_config.TextColumn("Fecha (AAAA-MM-DD)"),
                "Tipo": st.column_config.SelectboxColumn("Tipo", options=["Gasto", "Ingreso", "Ahorro"]),
                "Monto": st.column_config.NumberColumn("Monto ($)", format="$%.2f", step=100.0),
            },
            key="editor_financiero",
        )
        b1, b2 = st.columns([1, 3])
        with b1:
            if st.button("💾 Guardar cambios", type="primary", use_container_width=True):
                exito, mensaje = guardar_hoja(
                    HOJA_MOVIMIENTOS, solo_columnas_mov(normalizar_movimientos(df_admin)), COLS_MOV)
                if exito:
                    st.session_state.modo_revision = False
                    st.session_state.ignorar_alerta_cuadre = False
                    st.success(mensaje)
                    st.rerun()
                else:
                    st.error(mensaje)
        with b2:
            if st.button("🔄 Recargar desde Google Sheets", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
