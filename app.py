import streamlit as st
import pandas as pd
import os
import shutil
from datetime import datetime, date

ARCHIVO_CSV = "registro_financiero.csv"
BACKUP_DIR = "backups"
META_ESPANA = 522800

st.set_page_config(page_title="Proyecto España 2028", page_icon="🇪🇸", layout="wide")

# Función segura para respaldar y guardar archivos evitando bloqueos de Excel
def guardar_con_seguridad(df_a_guardar):
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
        if os.path.exists(ARCHIVO_CSV):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy(ARCHIVO_CSV, os.path.join(BACKUP_DIR, f"backup_{timestamp}.csv"))
        
        df_a_guardar.to_csv(ARCHIVO_CSV, index=False)
        return True, "¡Guardado exitosamente con respaldo automático!"
    except PermissionError:
        return False, "⚠️ Error: El archivo CSV está abierto en Excel u otro programa. Ciérralo e inténtalo de nuevo."
    except Exception as e:
        return False, f"⚠️ Error inesperado al guardar: {e}"

# Inicializar base de datos local
if not os.path.exists(ARCHIVO_CSV):
    df_inicial = pd.DataFrame(columns=["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion"])
    os.makedirs(BACKUP_DIR, exist_ok=True)
    df_inicial.to_csv(ARCHIVO_CSV, index=False)

try:
    df = pd.read_csv(ARCHIVO_CSV)
except Exception as e:
    st.error(f"Error al leer la base de datos: {e}")
    df = pd.DataFrame(columns=["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion"])

# Validar columnas base y formato de fecha
if not df.empty:
    df["Fecha_DT"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Anio"] = df["Fecha_DT"].dt.year.fillna(date.today().year).astype(int)
    df["Mes"] = df["Fecha_DT"].dt.month.fillna(date.today().month).astype(int)
    if "ID" not in df.columns:
        df["ID"] = [str(i) for i in range(len(df))]
        guardar_con_seguridad(df.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore"))
else:
    df = pd.DataFrame(columns=["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion"])

st.title("🇪🇸 Tablero Financiero: Proyecto España 2028")

# Control de estado para los modos de revisión y ocultar alerta
if "modo_revision" not in st.session_state:
    st.session_state.modo_revision = False
if "ignorar_alerta_cuadre" not in st.session_state:
    st.session_state.ignorar_alerta_cuadre = False

# --- SECCIÓN PRINCIPAL: FILTROS INTERACTIVOS ---
st.subheader("🔍 Selector de Periodos y Acumulados")

if not df.empty:
    df["Fecha_DT"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Periodo_Label"] = df["Fecha_DT"].dt.strftime('%Y - %B')
    
    periodos_disponibles = sorted(df["Periodo_Label"].dropna().unique(), reverse=True)
    mes_actual_label = pd.Timestamp.now().strftime('%Y - %B')
    default_selection = [mes_actual_label] if mes_actual_label in periodos_disponibles else (periodos_disponibles[:1] if periodos_disponibles else [])
    
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        meses_seleccionados = st.multiselect(
            "Selecciona los meses a sumar (Acumulado flexible):",
            options=periodos_disponibles,
            default=default_selection,
            help="Selecciona uno, varios o meses salteados para acumular sus montos."
        )
    with col_f2:
        anios_disponibles = sorted(df["Anio"].dropna().unique(), reverse=True) if "Anio" in df.columns else [date.today().year]
        if not anios_disponibles:
            anios_disponibles = [date.today().year]
        anio_seleccionado = st.selectbox("Seleccionar Año para Resumen Anual:", options=anios_disponibles, index=0)

    df_filtrado = df[df["Periodo_Label"].isin(meses_seleccionados)]
else:
    df_filtrado = pd.DataFrame(columns=df.columns)
    anios_disponibles = [date.today().year]
    anio_seleccionado = date.today().year

st.divider()

# --- CÁLCULOS PARA VALIDACIÓN DE CUADRE ---
total_ingresos_f = df_filtrado[df_filtrado["Tipo"] == "Ingreso"]["Monto"].sum() if not df_filtrado.empty else 0.0
total_gastos_f = df_filtrado[df_filtrado["Tipo"] == "Gasto"]["Monto"].sum() if not df_filtrado.empty else 0.0
total_ahorro_f = df_filtrado[df_filtrado["Tipo"] == "Ahorro"]["Monto"].sum() if not df_filtrado.empty else 0.0
suma_gastos_ahorro = total_gastos_f + total_ahorro_f

alerta_cuadre_activa = False
if total_ingresos_f > 0 and abs(suma_gastos_ahorro - total_ingresos_f) > 1.0:
    alerta_cuadre_activa = True

# --- BARRA LATERAL: REGISTRO DINÁMICO Y EXPORTACIÓN ---
if st.session_state.modo_revision:
    st.sidebar.warning("🔒 **Registro bloqueado**\n\nEstás en modo de revisión de historial para corregir errores.")
else:
    st.sidebar.header("📝 Registrar Movimiento")
    fecha = st.sidebar.date_input("Fecha", date.today())
    tipo = st.sidebar.selectbox("Tipo de Movimiento", ["Gasto", "Ingreso", "Ahorro"], key="tipo_mov")

    if tipo == "Gasto":
        categorias_gasto = ["Ocio", "Entretenimiento", "Servicios basicos", "Mandado", "Gasolina", "Universidad", "Prestamos o deudas", "Casa"]
        categoria = st.sidebar.selectbox("Categoría", categorias_gasto, key="cat_gasto")
    elif tipo == "Ahorro":
        categoria = st.sidebar.selectbox("Categoría", ["Fondo España"], key="cat_ahorro")
    else:
        categoria = st.sidebar.selectbox("Categoría", ["Sueldo Fijo", "Trabajos Extra"], key="cat_ingreso")
        
    monto = st.sidebar.number_input("Monto ($)", min_value=0.0, step=100.0, key="monto_mov")
    descripcion = st.sidebar.text_input("Descripción (Ej. Super, Gasolina, Cena)", key="desc_mov")

    if st.sidebar.button("Guardar Movimiento", type="primary"):
        if fecha.year < 2024 or fecha.year > 2035:
            st.sidebar.error("⚠️ El año seleccionado está fuera del rango válido (2024-2035).")
        elif monto <= 0:
            st.sidebar.error("⚠️ El monto debe ser mayor a 0.")
        else:
            nuevo_id = str(pd.Timestamp.now().timestamp())
            nuevo_row = pd.DataFrame({
                "ID": [nuevo_id],
                "Fecha": [str(fecha)],
                "Anio": [int(fecha.year)],
                "Mes": [int(fecha.month)],
                "Tipo": [tipo],
                "Categoria": [categoria],
                "Monto": [float(monto)],
                "Descripcion": [descripcion]
            })
            df_actualizado = pd.concat([df.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore"), nuevo_row], ignore_index=True)
            exito, mensaje = guardar_con_seguridad(df_actualizado)
            if exito:
                st.session_state.modo_revision = False
                st.session_state.ignorar_alerta_cuadre = False
                st.sidebar.success(mensaje)
                st.rerun()
            else:
                st.sidebar.error(mensaje)

st.sidebar.divider()
st.sidebar.header("📥 Descargar Respaldo")
if not df.empty:
    csv_export = df.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore").to_csv(index=False).encode('utf-8')
    st.sidebar.download_button(
        label="Descargar CSV de Respaldo",
        data=csv_export,
        file_name=f"respaldo_finanzas_{date.today().strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )

# --- ALERTA DE CUADRE A PÁGINA COMPLETA ---
if alerta_cuadre_activa and not st.session_state.ignorar_alerta_cuadre:
    st.markdown(
        f"""
        <div style="background-color: #ffe6e6; padding: 20px; border-radius: 10px; border: 1px solid #ff9999; margin-bottom: 20px;">
            <h4 style="color: #990000; margin-top: 0; margin-bottom: 10px;">🚨 ALERTA DE DESAJUSTE FINANCIERO EN EL PERIODO</h4>
            <p style="font-size: 16px; color: #721c24; margin-bottom: 0;">
                Los datos no cuadran: tus ingresos totales (<b>${total_ingresos_f:,.2f}</b>) no coinciden con la suma de tus gastos y ahorros (<b>${suma_gastos_ahorro:,.2f}</b>).
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )
    col_al1, col_al2 = st.columns(2)
    with col_al1:
        if st.button("📝 Los datos no cuadran porque faltan movimientos por registrar", type="primary", use_container_width=True):
            st.session_state.ignorar_alerta_cuadre = True
            st.session_state.modo_revision = False
            st.rerun()
    with col_al2:
        if st.button("🔍 Revisar el historial de movimientos", use_container_width=True):
            st.session_state.modo_revision = True
            st.session_state.ignorar_alerta_cuadre = False
            st.rerun()
    st.divider()

# --- PESTAÑAS DE NAVEGACIÓN PRINCIPAL ---
tab_mes, tab_anual, tab_admin = st.tabs(["📊 Resumen de Periodo / Acumulado", "📅 Resumen del Año (Ene - Dic)", "⚙️ Administrar Historial"])

with tab_mes:
    st.header("Resumen del Periodo Seleccionado")
    
    total_ingresos = total_ingresos_f
    total_gastos = total_gastos_f
    total_ahorro_mes = total_ahorro_f

    margen_libre = total_ingresos - total_gastos
    tasa_ahorro = (total_ahorro_mes / total_ingresos * 100) if total_ingresos > 0 else 0.0

    fondo_espana_historico = df[df["Categoria"] == "Fondo España"]["Monto"].sum() if not df.empty else 0.0
    faltante_meta = META_ESPANA - fondo_espana_historico

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Ingresos Totales", f"${total_ingresos:,.2f}")
    col2.metric("Gastos Totales", f"${total_gastos:,.2f}")
    col3.metric("Margen Libre", f"${margen_libre:,.2f}", delta=f"{tasa_ahorro:.1f}% tasa ahorro")
    col4.metric("Fondo España Acumulado", f"${fondo_espana_historico:,.2f}", delta=f"Faltan ${faltante_meta:,.2f}")

    if META_ESPANA > 0:
        st.progress(min(fondo_espana_historico / META_ESPANA, 1.0))

    if not df_filtrado.empty:
        limites_presupuesto = {
            "Entretenimiento": 298.0,
            "Mandado": 9000.0,
            "Ocio": 3000.0,
            "Prestamos o deudas": 3300.0
        }
        for categoria_obj, limite_val in limites_presupuesto.items():
            gasto_actual = df_filtrado[(df_filtrado["Tipo"] == "Gasto") & (df_filtrado["Categoria"] == categoria_obj)]["Monto"].sum()
            if gasto_actual > limite_val:
                st.warning(f"⚠️ Alerta de Presupuesto: El gasto en **{categoria_obj}** (${gasto_actual:,.2f}) superó el límite establecido de ${limite_val:,.2f} para los periodos seleccionados.")

    st.markdown("---")
    st.subheader("📈 Gráficas del Periodo")

    if not df_filtrado.empty:
        col_g1, col_g2 = st.columns(2)
        
        with col_g1:
            st.markdown("**Gastos por Categoría**")
            df_gastos = df_filtrado[df_filtrado["Tipo"] == "Gasto"]
            if not df_gastos.empty:
                gastos_cat = df_gastos.groupby("Categoria")["Monto"].sum().reset_index()
                st.bar_chart(data=gastos_cat, x="Categoria", y="Monto", color="Categoria")
            else:
                st.info("No hay gastos registrados en los meses seleccionados.")
                
        with col_g2:
            st.markdown("**Comparativa: Ingresos vs Gastos vs Ahorros**")
            df_comp = pd.DataFrame({
                "Concepto": ["Ingresos", "Gastos", "Ahorro España"],
                "Monto": [total_ingresos, total_gastos, total_ahorro_mes]
            })
            st.bar_chart(data=df_comp, x="Concepto", y="Monto", color="Concepto")
    else:
        st.info("Selecciona al menos un mes arriba para ver las gráficas.")

with tab_anual:
    st.header(f"📅 Resumen Anual: {anio_seleccionado} (Enero a Diciembre)")
    
    if not df.empty:
        df_anual = df[df["Anio"] == anio_seleccionado]
        
        ingresos_anio = df_anual[df_anual["Tipo"] == "Ingreso"]["Monto"].sum()
        gastos_anio = df_anual[df_anual["Tipo"] == "Gasto"]["Monto"].sum()
        ahorro_anio = df_anual[df_anual["Tipo"] == "Ahorro"]["Monto"].sum()
        margen_anio = ingresos_anio - gastos_anio
        tasa_ahorro_anio = (ahorro_anio / ingresos_anio * 100) if ingresos_anio > 0 else 0.0
        
        fondo_espana_historico = df[df["Categoria"] == "Fondo España"]["Monto"].sum() if not df.empty else 0.0
        faltante_meta = META_ESPANA - fondo_espana_historico
        
        col_a1, col_a2, col_a3, col_a4, col_a5 = st.columns(5)
        col_a1.metric("Ingresos Anuales", f"${ingresos_anio:,.2f}")
        col_a2.metric("Gastos Anuales", f"${gastos_anio:,.2f}")
        col_a3.metric("Ahorro Anual", f"${ahorro_anio:,.2f}")
        col_a4.metric("Tasa de Ahorro", f"{tasa_ahorro_anio:.1f}%")
        col_a5.metric("Faltante Meta", f"${faltante_meta:,.2f}")
        
        st.markdown("---")
        
        modo_visual = st.radio(
            "Selecciona el modo de visualización anual:",
            ["Mes a Mes (Detalle)", "Periodo Completo Anual (Acumulado)"],
            horizontal=True,
            key="modo_visual_anual"
        )
        
        meses_nombres = {
            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 
            5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 
            9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
        }
        
        if not df_anual.empty:
            tabla_mensual = df_anual.groupby(["Mes", "Tipo"])["Monto"].sum().unstack(fill_value=0).reset_index()
            for col in ["Ingreso", "Gasto", "Ahorro"]:
                if col not in tabla_mensual.columns:
                    tabla_mensual[col] = 0.0
                    
            tabla_mensual["Nombre Mes"] = tabla_mensual["Mes"].map(meses_nombres)
            
            todos_meses_df = pd.DataFrame({"Mes": range(1, 13)})
            todos_meses_df["Nombre Mes"] = todos_meses_df["Mes"].map(meses_nombres)
            tabla_completa = pd.merge(todos_meses_df, tabla_mensual, on=["Mes", "Nombre Mes"], how="left").fillna(0)
            
            # Forzar categoría ordenada para que la gráfica de líneas respete estrictamente el orden cronológico
            orden_meses = list(meses_nombres.values())
            tabla_completa["Nombre Mes"] = pd.Categorical(tabla_completa["Nombre Mes"], categories=orden_meses, ordered=True)
            tabla_completa = tabla_completa.sort_values("Mes")
            
            st.markdown("### 📈 Gráficas Globales del Año")
            cg1, cg2 = st.columns(2)
            with cg1:
                st.markdown("**Gastos por Categoría (Anual)**")
                df_gastos_anual = df_anual[df_anual["Tipo"] == "Gasto"]
                if not df_gastos_anual.empty:
                    gastos_cat_anual = df_gastos_anual.groupby("Categoria")["Monto"].sum().reset_index()
                    st.bar_chart(data=gastos_cat_anual, x="Categoria", y="Monto", color="Categoria")
                else:
                    st.info("No hay gastos registrados este año.")
            with cg2:
                st.markdown("**Comparativa: Ingresos vs Gastos vs Ahorros (Anual)**")
                df_comp_anual = pd.DataFrame({
                    "Concepto": ["Ingresos", "Gastos", "Ahorro España"],
                    "Monto": [ingresos_anio, gastos_anio, ahorro_anio]
                })
                st.bar_chart(data=df_comp_anual, x="Concepto", y="Monto", color="Concepto")
                
            st.markdown("---")
            
            if modo_visual == "Mes a Mes (Detalle)":
                st.subheader("📊 Tabla de Comportamiento Mensual (Enero a Diciembre)")
                st.dataframe(
                    tabla_completa[["Mes", "Nombre Mes", "Ingreso", "Gasto", "Ahorro"]].rename(
                        columns={"Ingreso": "Ingresos ($)", "Gasto": "Gastos ($)", "Ahorro": "Ahorro ($)"}
                    ),
                    use_container_width=True,
                    hide_index=True
                )
                
                st.markdown("### 📈 Gráfica de Líneas: Evolución de Ingresos, Gastos y Ahorro por Mes")
                chart_linea_data = tabla_completa.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]]
                st.line_chart(chart_linea_data)
            else:
                st.subheader("📦 Consolidado del Periodo Anual Completo")
                
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("Margen Libre Acumulado Anual", f"${margen_anio:,.2f}", delta=f"{tasa_ahorro_anio:.1f}% Tasa Ahorro")
                mc2.metric("Total Ahorrado en el Año", f"${ahorro_anio:,.2f}")
                mc3.metric("Faltante Restante Meta España", f"${faltante_meta:,.2f}", delta=f"Meta: ${META_ESPANA:,.0f}", delta_color="off")
                
                st.markdown("### 📈 Gráfica de Líneas: Tendencia Anual Completa")
                chart_linea_data = tabla_completa.set_index("Nombre Mes")[["Ingreso", "Gasto", "Ahorro"]]
                st.line_chart(chart_linea_data)
        else:
            st.info(f"No hay registros todavía para el año {anio_seleccionado}.")
    else:
        st.info("Aún no hay datos cargados en el sistema.")

with tab_admin:
    st.header("⚙️ Administrar Historial de Movimientos")
    st.markdown("Puedes editar directamente los valores en la tabla o eliminar filas seleccionándolas.")

    if not df.empty:
        df_clean = df.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore")
        df_editado = st.data_editor(
            df_clean,
            num_rows="dynamic",
            use_container_width=True,
            key="editor_financiero"
        )
        
        if st.button("Guardar Cambios en la Base de Datos"):
            df_editado["Fecha_DT"] = pd.to_datetime(df_editado["Fecha"], errors="coerce")
            df_editado["Anio"] = df_editado["Fecha_DT"].dt.year.fillna(date.today().year).astype(int)
            df_editado["Mes"] = df_editado["Fecha_DT"].dt.month.fillna(date.today().month).astype(int)
            df_final = df_editado.drop(columns=["Fecha_DT"], errors="ignore")
            exito, mensaje = guardar_con_seguridad(df_final)
            if exito:
                st.session_state.modo_revision = False
                st.session_state.ignorar_alerta_cuadre = False
                st.success(mensaje)
                st.rerun()
            else:
                st.error(mensaje)
    else:
        st.info("Aún no hay movimientos registrados.")