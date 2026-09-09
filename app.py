import streamlit as st
import pandas as pd
from datetime import datetime, date
from streamlit_gsheets import GSheetsConnection

# =============================================================
# CONFIGURACIÓN GENERAL
# =============================================================
META_ESPANA = 522800

# OJO: este es el nombre de la PESTAÑA (tab) dentro del archivo de Google Sheets,
# no el nombre del archivo. Si tu pestaña se llama "Hoja 1" o "Sheet1", cámbialo aquí.
WORKSHEET = "registro_financiero"

COLUMNAS = ["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion"]

CATEGORIAS_GASTO = [
    "Ocio", "Entretenimiento", "Servicios basicos", "Mandado",
    "Gasolina", "Universidad", "Prestamos o deudas", "Casa",
]

st.set_page_config(page_title="Proyecto España 2028", page_icon="🇪🇸", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)


# =============================================================
# UTILIDADES DE DATOS
# =============================================================
def df_vacio() -> pd.DataFrame:
    """DataFrame vacío con el esquema correcto y tipos estables."""
    vacio = pd.DataFrame(columns=COLUMNAS)
    vacio["Monto"] = vacio["Monto"].astype(float)
    vacio["Anio"] = vacio["Anio"].astype("Int64")
    vacio["Mes"] = vacio["Mes"].astype("Int64")
    return vacio


def normalizar(datos: pd.DataFrame) -> pd.DataFrame:
    """Garantiza columnas, tipos y campos derivados. Nunca falla por columnas ausentes."""
    if datos is None or datos.empty:
        return df_vacio()

    datos = datos.copy()

    # Google Sheets suele devolver filas y columnas fantasma completamente vacías
    datos = datos.dropna(how="all")
    datos = datos.loc[:, ~datos.columns.astype(str).str.startswith("Unnamed")]

    for col in COLUMNAS:
        if col not in datos.columns:
            datos[col] = None

    # Monto SIEMPRE numérico (la hoja lo puede devolver como texto "1,500")
    datos["Monto"] = (
        datos["Monto"].astype(str)
        .str.replace(r"[^0-9\.\-]", "", regex=True)
        .replace("", None)
    )
    datos["Monto"] = pd.to_numeric(datos["Monto"], errors="coerce").fillna(0.0)

    # Fecha normalizada
    fecha_dt = pd.to_datetime(datos["Fecha"], errors="coerce")
    datos = datos[fecha_dt.notna()].copy()
    fecha_dt = fecha_dt[fecha_dt.notna()]

    if datos.empty:
        return df_vacio()

    datos["Fecha"] = fecha_dt.dt.strftime("%Y-%m-%d")
    datos["Fecha_DT"] = fecha_dt
    datos["Anio"] = fecha_dt.dt.year.astype(int)
    datos["Mes"] = fecha_dt.dt.month.astype(int)
    datos["Periodo_Label"] = fecha_dt.dt.strftime("%Y - %m")

    # Texto limpio
    for col in ["Tipo", "Categoria", "Descripcion"]:
        datos[col] = datos[col].fillna("").astype(str).str.strip()

    # IDs faltantes o duplicados
    datos["ID"] = datos["ID"].astype(str).replace({"nan": "", "None": ""})
    faltantes = datos["ID"] == ""
    if faltantes.any():
        base = int(pd.Timestamp.now().timestamp())
        datos.loc[faltantes, "ID"] = [str(base + i) for i in range(int(faltantes.sum()))]

    return datos.reset_index(drop=True)


def para_guardar(datos: pd.DataFrame) -> pd.DataFrame:
    """Deja solo las columnas reales de la hoja, en orden fijo."""
    limpio = datos.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore").copy()
    for col in COLUMNAS:
        if col not in limpio.columns:
            limpio[col] = None
    return limpio[COLUMNAS].reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner="Cargando datos desde Google Sheets...")
def leer_hoja():
    return conn.read(worksheet=WORKSHEET)


def guardar_en_nube(df_a_guardar: pd.DataFrame):
    try:
        conn.update(worksheet=WORKSHEET, data=para_guardar(df_a_guardar))
        st.cache_data.clear()
        return True, "✅ ¡Guardado exitosamente en la nube!"
    except Exception as e:
        return False, f"⚠️ Error al guardar en Google Sheets: {e}"


# =============================================================
# CARGA DE DATOS
# =============================================================
error_conexion = None
try:
    df = normalizar(leer_hoja())
except Exception as e:
    error_conexion = e
    df = df_vacio()

st.title("🇪🇸 Tablero Financiero: Proyecto España 2028")

if error_conexion is not None:
    st.error(
        f"No se pudo leer la hoja **{WORKSHEET}**.\n\n"
        f"Detalle técnico: `{error_conexion}`\n\n"
        "Revisa que en los *Secrets* el campo `spreadsheet` sea la **URL completa** del archivo, "
        "que la pestaña se llame exactamente igual que `WORKSHEET`, y que la hoja esté compartida "
        "como **Editor** con la cuenta de servicio."
    )

# Estados de sesión
st.session_state.setdefault("modo_revision", False)
st.session_state.setdefault("ignorar_alerta_cuadre", False)

if "presupuestos_items" not in st.session_state:
    st.session_state.presupuestos_items = pd.DataFrame(
        [
            {"Categoria": "Ocio", "Detalle": "General", "Monto": 3000.0, "Expiracion": pd.NaT},
            {"Categoria": "Entretenimiento", "Detalle": "General", "Monto": 298.0, "Expiracion": pd.NaT},
            {"Categoria": "Mandado", "Detalle": "General", "Monto": 9000.0, "Expiracion": pd.NaT},
            {"Categoria": "Servicios basicos", "Detalle": "General", "Monto": 1500.0, "Expiracion": pd.NaT},
            {"Categoria": "Gasolina", "Detalle": "General", "Monto": 2000.0, "Expiracion": pd.NaT},
            {"Categoria": "Universidad", "Detalle": "General", "Monto": 3000.0, "Expiracion": pd.NaT},
            {"Categoria": "Prestamos o deudas", "Detalle": "Deuda Fija", "Monto": 1300.0, "Expiracion": pd.NaT},
            {"Categoria": "Prestamos o deudas", "Detalle": "Préstamo a liquidar", "Monto": 2000.0,
             "Expiracion": pd.Timestamp("2025-12-31")},
            {"Categoria": "Casa", "Detalle": "General", "Monto": 5000.0, "Expiracion": pd.NaT},
        ]
    )
    st.session_state.presupuestos_items["Expiracion"] = pd.to_datetime(
        st.session_state.presupuestos_items["Expiracion"], errors="coerce"
    )


def calcular_presupuestos_activos():
    """Suma los montos por categoría ignorando los que ya vencieron."""
    hoy = pd.Timestamp(date.today())
    activos, expirados = {}, []

    for _, row in st.session_state.presupuestos_items.iterrows():
        cat = row.get("Categoria")
        if pd.isna(cat) or str(cat).strip() == "":
            continue

        detalle = row.get("Detalle") if pd.notna(row.get("Detalle")) else ""
        monto = row.get("Monto")
        monto = 0.0 if pd.isna(monto) else float(monto)

        exp = pd.to_datetime(row.get("Expiracion"), errors="coerce")
        if pd.notna(exp) and hoy > exp:
            expirados.append(f"{cat} ({detalle})")
            continue

        activos[cat] = activos.get(cat, 0.0) + monto

    return activos, expirados


presupuestos_activos, expirados = calcular_presupuestos_activos()


# =============================================================
# FILTROS
# =============================================================
st.subheader("🔍 Selector de Periodos y Acumulados")

MESES_NOMBRES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

if not df.empty:
    periodos_disponibles = sorted(df["Periodo_Label"].dropna().unique(), reverse=True)
    etiqueta_actual = pd.Timestamp.now().strftime("%Y - %m")
    if etiqueta_actual in periodos_disponibles:
        seleccion_default = [etiqueta_actual]
    else:
        seleccion_default = periodos_disponibles[:1]

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        meses_seleccionados = st.multiselect(
            "Selecciona los meses a sumar (acumulado flexible):",
            options=periodos_disponibles,
            default=seleccion_default,
            format_func=lambda p: f"{p.split(' - ')[0]} - {MESES_NOMBRES[int(p.split(' - ')[1])]}",
        )
    with col_f2:
        anios_disponibles = sorted(df["Anio"].dropna().unique().tolist(), reverse=True)
        anio_seleccionado = st.selectbox("Año para el resumen anual:", options=anios_disponibles, index=0)

    df_filtrado = df[df["Periodo_Label"].isin(meses_seleccionados)]
else:
    st.info("Aún no hay movimientos registrados. Usa la barra lateral para capturar el primero.")
    df_filtrado = df_vacio()
    anio_seleccionado = date.today().year

st.divider()


def suma_por_tipo(datos: pd.DataFrame, tipo: str) -> float:
    if datos.empty:
        return 0.0
    return float(datos.loc[datos["Tipo"] == tipo, "Monto"].sum())


total_ingresos_f = suma_por_tipo(df_filtrado, "Ingreso")
total_gastos_f = suma_por_tipo(df_filtrado, "Gasto")
total_ahorro_f = suma_por_tipo(df_filtrado, "Ahorro")
suma_gastos_ahorro = total_gastos_f + total_ahorro_f

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
    descripcion = st.sidebar.text_input("Descripción (ej. súper, gasolina, cena)", key="desc_mov")

    if st.sidebar.button("Guardar Movimiento", type="primary", use_container_width=True):
        if not (2024 <= fecha.year <= 2035):
            st.sidebar.error("⚠️ El año seleccionado está fuera del rango válido.")
        elif monto <= 0:
            st.sidebar.error("⚠️ El monto debe ser mayor a 0.")
        else:
            nuevo_row = pd.DataFrame([{
                "ID": str(int(pd.Timestamp.now().timestamp() * 1000)),
                "Fecha": fecha.strftime("%Y-%m-%d"),
                "Anio": int(fecha.year),
                "Mes": int(fecha.month),
                "Tipo": tipo,
                "Categoria": categoria,
                "Monto": float(monto),
                "Descripcion": descripcion,
            }])
            df_actualizado = pd.concat([para_guardar(df), nuevo_row], ignore_index=True)
            exito, mensaje = guardar_en_nube(df_actualizado)
            if exito:
                st.session_state.modo_revision = False
                st.session_state.ignorar_alerta_cuadre = False
                st.sidebar.success(mensaje)
                st.rerun()
            else:
                st.sidebar.error(mensaje)

st.sidebar.divider()
st.sidebar.caption(f"Registros en la hoja: {len(df)}")


# =============================================================
# ALERTA DE CUADRE
# =============================================================
if alerta_cuadre_activa and not st.session_state.ignorar_alerta_cuadre:
    st.error(
        "🚨 **Desajuste financiero en el periodo**\n\n"
        f"Tus ingresos (**${total_ingresos_f:,.2f}**) no coinciden con la suma de gastos y ahorros "
        f"(**${suma_gastos_ahorro:,.2f}**). Diferencia: **${total_ingresos_f - suma_gastos_ahorro:,.2f}**."
    )

    col_al1, col_al2, col_al3 = st.columns(3)
    with col_al1:
        if st.button("📝 Faltan movimientos por registrar", type="primary", use_container_width=True):
            st.session_state.ignorar_alerta_cuadre = True
            st.session_state.modo_revision = False
            st.rerun()
    with col_al2:
        if st.button("💡 Faltan gastos por hacer", use_container_width=True):
            st.session_state.ignorar_alerta_cuadre = True
            st.session_state.modo_revision = False
            st.rerun()
    with col_al3:
        if st.button("🔍 Revisar el historial", use_container_width=True):
            st.session_state.modo_revision = True
            st.session_state.ignorar_alerta_cuadre = False
            st.rerun()
    st.divider()


# =============================================================
# PESTAÑAS
# =============================================================
tab_mes, tab_anual, tab_presupuestos, tab_admin = st.tabs(
    ["📊 Resumen de Periodo", "📅 Resumen del Año", "💰 Presupuestos", "⚙️ Administrar Historial"]
)

with tab_mes:
    st.header("Resumen del periodo seleccionado")

    margen_libre = total_ingresos_f - total_gastos_f
    tasa_ahorro = (total_ahorro_f / total_ingresos_f * 100) if total_ingresos_f > 0 else 0.0

    fondo_espana = float(df.loc[df["Categoria"] == "Fondo España", "Monto"].sum()) if not df.empty else 0.0
    faltante_meta = max(META_ESPANA - fondo_espana, 0.0)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ingresos totales", f"${total_ingresos_f:,.2f}")
    col2.metric("Gastos totales", f"${total_gastos_f:,.2f}")
    col3.metric("Margen libre", f"${margen_libre:,.2f}", delta=f"{tasa_ahorro:.1f}% tasa de ahorro")
    col4.metric("Fondo España acumulado", f"${fondo_espana:,.2f}", delta=f"Faltan ${faltante_meta:,.2f}")

    avance = 0.0 if META_ESPANA <= 0 else min(max(fondo_espana / META_ESPANA, 0.0), 1.0)
    st.progress(avance, text=f"Avance hacia la meta: {avance * 100:.1f}%")

    if not df_filtrado.empty:
        for cat_obj, lim_val in presupuestos_activos.items():
            gasto_act = float(
                df_filtrado.loc[
                    (df_filtrado["Tipo"] == "Gasto") & (df_filtrado["Categoria"] == cat_obj), "Monto"
                ].sum()
            )
            if lim_val > 0 and gasto_act > lim_val:
                st.warning(
                    f"⚠️ **{cat_obj}**: gastaste ${gasto_act:,.2f} y tu límite activo es ${lim_val:,.2f} "
                    f"(excedente de ${gasto_act - lim_val:,.2f})."
                )

    st.markdown("---")
    st.subheader("📈 Gráficas del periodo")

    if df_filtrado.empty:
        st.info("Selecciona al menos un periodo con movimientos para ver las gráficas.")
    else:
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.markdown("**Gastos por categoría**")
            df_gastos = df_filtrado[df_filtrado["Tipo"] == "Gasto"]
            if df_gastos.empty:
                st.info("No hay gastos registrados en este periodo.")
            else:
                resumen_gastos = df_gastos.groupby("Categoria", as_index=False)["Monto"].sum()
                st.bar_chart(resumen_gastos, x="Categoria", y="Monto")
        with col_g2:
            st.markdown("**Ingresos vs gastos vs ahorro**")
            comparativa = pd.DataFrame(
                {"Monto": [total_ingresos_f, total_gastos_f, total_ahorro_f]},
                index=["Ingresos", "Gastos", "Ahorro España"],
            )
            st.bar_chart(comparativa)

with tab_anual:
    st.header(f"📅 Resumen anual: {anio_seleccionado}")

    df_anual = df[df["Anio"] == anio_seleccionado] if not df.empty else df_vacio()

    ingresos_anio = suma_por_tipo(df_anual, "Ingreso")
    gastos_anio = suma_por_tipo(df_anual, "Gasto")
    ahorro_anio = suma_por_tipo(df_anual, "Ahorro")
    tasa_ahorro_anio = (ahorro_anio / ingresos_anio * 100) if ingresos_anio > 0 else 0.0
    fondo_total = float(df.loc[df["Categoria"] == "Fondo España", "Monto"].sum()) if not df.empty else 0.0

    col_a1, col_a2, col_a3, col_a4, col_a5 = st.columns(5)
    col_a1.metric("Ingresos anuales", f"${ingresos_anio:,.2f}")
    col_a2.metric("Gastos anuales", f"${gastos_anio:,.2f}")
    col_a3.metric("Ahorro anual", f"${ahorro_anio:,.2f}")
    col_a4.metric("Tasa de ahorro", f"{tasa_ahorro_anio:.1f}%")
    col_a5.metric("Faltante meta", f"${max(META_ESPANA - fondo_total, 0.0):,.2f}")

    st.markdown("---")

    if df_anual.empty:
        st.info(f"No hay movimientos registrados en {anio_seleccionado}.")
    else:
        modo_visual = st.radio(
            "Modo de visualización:",
            ["Mes a mes (detalle)", "Acumulado del año"],
            horizontal=True,
        )

        tabla = df_anual.groupby(["Mes", "Tipo"])["Monto"].sum().unstack(fill_value=0.0).reset_index()
        for col in ["Ingreso", "Gasto", "Ahorro"]:
            if col not in tabla.columns:
                tabla[col] = 0.0

        todos = pd.DataFrame({"Mes": range(1, 13)})
        tabla = todos.merge(tabla, on="Mes", how="left").fillna(0.0)
        tabla["Nombre Mes"] = tabla["Mes"].map(MESES_NOMBRES)
        tabla = tabla.sort_values("Mes")

        if modo_visual == "Mes a mes (detalle)":
            st.dataframe(
                tabla[["Nombre Mes", "Ingreso", "Gasto", "Ahorro"]].rename(
                    columns={"Nombre Mes": "Mes", "Ingreso": "Ingresos ($)",
                             "Gasto": "Gastos ($)", "Ahorro": "Ahorro ($)"}
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.line_chart(tabla.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]])
        else:
            acumulado = tabla.copy()
            acumulado[["Ingreso", "Gasto", "Ahorro"]] = acumulado[["Ingreso", "Gasto", "Ahorro"]].cumsum()
            st.line_chart(acumulado.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]])

with tab_presupuestos:
    st.header("💰 Control de partidas y vencimientos")
    st.markdown(
        "Divide cada categoría en los conceptos que quieras y ponles fecha de expiración. "
        "Cuando la fecha pase, el monto deja de contar automáticamente."
    )

    cat_seleccionada = st.selectbox("📂 Categoría a revisar o editar:", CATEGORIAS_GASTO)

    df_cat = st.session_state.presupuestos_items
    df_cat = df_cat[df_cat["Categoria"] == cat_seleccionada][["Detalle", "Monto", "Expiracion"]].reset_index(drop=True)

    df_editado = st.data_editor(
        df_cat,
        column_config={
            "Detalle": st.column_config.TextColumn("Concepto", required=True),
            "Monto": st.column_config.NumberColumn("Monto ($)", min_value=0.0, format="$%.2f", step=100.0, required=True),
            "Expiracion": st.column_config.DateColumn("Expira el (opcional)", format="YYYY-MM-DD"),
        },
        use_container_width=True,
        num_rows="dynamic",
        key=f"editor_presupuesto_{cat_seleccionada}",
    )

    if st.button("💾 Aplicar cambios a esta categoría", type="primary"):
        resto = st.session_state.presupuestos_items[
            st.session_state.presupuestos_items["Categoria"] != cat_seleccionada
        ].copy()

        nuevo = df_editado.copy()
        nuevo = nuevo[nuevo["Detalle"].notna() & (nuevo["Detalle"].astype(str).str.strip() != "")]
        if not nuevo.empty:
            nuevo["Categoria"] = cat_seleccionada
            nuevo["Monto"] = pd.to_numeric(nuevo["Monto"], errors="coerce").fillna(0.0)
            nuevo["Expiracion"] = pd.to_datetime(nuevo["Expiracion"], errors="coerce")
            st.session_state.presupuestos_items = pd.concat([resto, nuevo], ignore_index=True)
        else:
            st.session_state.presupuestos_items = resto

        st.success(f"Presupuesto de {cat_seleccionada} actualizado.")
        st.rerun()

    st.divider()
    st.subheader("📋 Resumen mensual y apartado semanal (solo montos activos)")

    if presupuestos_activos:
        resumen = pd.DataFrame(
            [
                {
                    "Categoría": cat,
                    "Límite mensual activo": f"${lim:,.2f}",
                    "Apartado semanal (÷4)": f"${lim / 4:,.2f}",
                }
                for cat, lim in sorted(presupuestos_activos.items())
            ]
        )
        st.dataframe(resumen, use_container_width=True, hide_index=True)
    else:
        st.info("No tienes presupuestos activos configurados.")

    if expirados:
        st.success(f"✅ Montos ya expirados y descontados del cálculo: **{', '.join(expirados)}**")

    st.divider()
    st.markdown("### 📊 Totales globales estimados")
    total_mensual = sum(presupuestos_activos.values())
    col_t1, col_t2 = st.columns(2)
    col_t1.metric("💰 Presupuesto mensual global", f"${total_mensual:,.2f}")
    col_t2.metric("📅 Total semanal a apartar", f"${total_mensual / 4:,.2f}")

    st.caption("Nota: los presupuestos viven en la sesión. Si recargas la página vuelven a los valores base.")

with tab_admin:
    st.header("⚙️ Administrar historial de movimientos")

    if df.empty:
        st.info("No hay movimientos que administrar todavía.")
    else:
        st.caption("Edita celdas, agrega filas o borra las que sobren. Nada se envía a la hoja hasta que guardes.")

        df_admin = st.data_editor(
            para_guardar(df),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "ID": st.column_config.TextColumn("ID", disabled=True),
                "Fecha": st.column_config.TextColumn("Fecha (YYYY-MM-DD)"),
                "Tipo": st.column_config.SelectboxColumn("Tipo", options=["Gasto", "Ingreso", "Ahorro"]),
                "Monto": st.column_config.NumberColumn("Monto ($)", format="$%.2f", step=100.0),
            },
            key="editor_financiero",
        )

        col_ad1, col_ad2 = st.columns([1, 3])
        with col_ad1:
            if st.button("💾 Guardar cambios en la nube", type="primary", use_container_width=True):
                exito, mensaje = guardar_en_nube(normalizar(df_admin))
                if exito:
                    st.session_state.modo_revision = False
                    st.session_state.ignorar_alerta_cuadre = False
                    st.success(mensaje)
                    st.rerun()
                else:
                    st.error(mensaje)
        with col_ad2:
            if st.button("🔄 Recargar desde Google Sheets", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
