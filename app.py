import hashlib
import time
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
from calendar import monthrange
from streamlit_gsheets import GSheetsConnection

# =============================================================
# CONFIGURACIÓN GENERAL
# =============================================================
META_ESPANA = 522800
FECHA_META_DEFAULT = date(2028, 12, 31)
MAX_BITACORA = 5000          # filas que se conservan en la hoja de bitácora
REINTENTOS = 3               # intentos ante error de cuota de la API

HOJA_MOVIMIENTOS = "registro_financiero"
HOJA_PRESUPUESTOS = "presupuestos"
HOJA_COBROS = "cobros"
HOJA_COMPROMISOS = "compromisos"
HOJA_BITACORA = "bitacora"

COLS_MOV = ["ID", "Fecha", "Anio", "Mes", "Tipo", "Categoria", "Monto", "Descripcion", "RefID"]
COLS_PRE = ["ID", "Categoria", "Detalle", "Monto", "Frecuencia", "DiaPago", "Inicio", "Expiracion"]
COLS_COB = ["ID", "Concepto", "Monto", "Frecuencia", "DiaCobro", "Inicio", "Expiracion"]
COLS_COMP = ["ID", "Categoria", "Concepto", "MontoProgramado", "FechaProgramada", "Estado",
             "FechaPago", "MontoPagado", "MovimientoID", "Origen", "Notas"]
COLS_BIT = ["Timestamp", "Usuario", "Accion", "Tabla", "RegistroID", "Campo",
            "ValorAnterior", "ValorNuevo", "Detalle"]

COLUMNAS_NUMERICAS = {"Monto", "MontoProgramado", "MontoPagado", "Anio", "Mes"}
COLUMNAS_FECHA = {"Fecha", "Inicio", "Expiracion", "FechaProgramada", "FechaPago"}
# Columnas de texto que Google Sheets devuelve como número ("15" llega como "15.0").
# Se normalizan igual en memoria y al leer, o la verificación de concurrencia falla siempre.
COLUMNAS_CLAVE = {"ID", "RefID", "MovimientoID", "Origen", "DiaPago", "DiaCobro", "RegistroID"}

ESTADOS = ["Planeado", "Comprometido", "Pagado", "Cancelado"]
ESTADO_ICONO = {"Planeado": "⚪ Planeado", "Comprometido": "🟡 Comprometido",
                "Pagado": "🟢 Pagado", "Cancelado": "⚫ Cancelado"}

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
# UTILIDADES BÁSICAS
# =============================================================
def esqueleto(columnas):
    vacio = pd.DataFrame(columns=columnas)
    for col in COLUMNAS_NUMERICAS & set(columnas):
        vacio[col] = vacio[col].astype(float)
    return vacio


def a_numero(serie):
    limpio = (
        serie.astype(str)
        .str.replace(r"[^0-9\.\-]", "", regex=True)
        .replace("", None)
    )
    return pd.to_numeric(limpio, errors="coerce").fillna(0.0)


def texto_simple(serie):
    return serie.fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})


def texto_dia(serie):
    return texto_simple(serie).str.replace(r"\.0$", "", regex=True)


def _a_fecha(valor):
    f = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(f) else f.date()


def nuevo_id(offset=0):
    return str(int(pd.Timestamp.now().timestamp() * 1000) + offset)


def rellenar_ids(tabla):
    tabla = tabla.copy()
    tabla["ID"] = [
        str(v).strip() if str(v).strip() not in ("", "nan", "None") else nuevo_id(n)
        for n, v in enumerate(tabla["ID"])
    ]
    return tabla


def _sin_latex(texto):
    """
    Streamlit interpreta el texto entre dos '$' como fórmula LaTeX, así que
    '**$2,800.00** y de **$0.00**' se rompe. Escapando el signo, se muestra tal cual.
    """
    return texto.replace("$", "\\$") if isinstance(texto, str) else texto


def msg_error(c, texto):
    c.error(_sin_latex(texto))


def msg_warning(c, texto):
    c.warning(_sin_latex(texto))


def msg_success(c, texto):
    c.success(_sin_latex(texto))


def msg_info(c, texto):
    c.info(_sin_latex(texto))


def msg_caption(c, texto):
    c.caption(_sin_latex(texto))


def id_desde_contenido(fila, columnas, posicion):
    """
    ID reproducible para filas que llegaron sin ID desde la hoja.
    Debe ser determinista: si cada recarga generara uno distinto, la verificación
    de concurrencia detectaría un 'cambio' que en realidad nunca ocurrió.
    """
    base = f"{posicion}|" + "|".join(str(fila.get(c, "")) for c in columnas if c != "ID")
    return "auto-" + hashlib.md5(base.encode("utf-8")).hexdigest()[:10]


def reparar_ids(datos, columnas):
    faltantes = datos["ID"] == ""
    if faltantes.any():
        datos.loc[faltantes, "ID"] = [
            id_desde_contenido(datos.iloc[i], columnas, i)
            for i in range(len(datos)) if faltantes.iloc[i]
        ]
    return datos


def con_relleno(destino, columnas, filas_remotas):
    """
    conn.update sobrescribe solo el rango que escribe. Si el nuevo contenido tiene
    menos filas que la hoja, las viejas se quedarían abajo. Se rellena con vacíos
    para que borrar de verdad borre.
    """
    faltan = int(filas_remotas) - len(destino)
    if faltan <= 0:
        return destino
    vacias = pd.DataFrame("", index=range(faltan), columns=list(columnas))
    return pd.concat([destino, vacias], ignore_index=True)


def con_reintentos(funcion, etiqueta=""):
    """Reintenta ante errores de cuota (429) o fallos transitorios de red."""
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            return funcion()
        except Exception as e:
            ultimo = e
            texto = str(e).lower()
            recuperable = any(p in texto for p in ("429", "quota", "rate", "timeout",
                                                   "503", "500", "temporarily"))
            if not recuperable or intento == REINTENTOS - 1:
                raise
            time.sleep(2 ** intento)
    raise ultimo


# =============================================================
# NORMALIZACIÓN PARA ESCRITURA Y HUELLA
# =============================================================
def para_escribir(datos, columnas):
    limpio = datos.copy()
    for col in columnas:
        if col not in limpio.columns:
            limpio[col] = None
    limpio = limpio[columnas].reset_index(drop=True)

    for col in COLUMNAS_FECHA & set(columnas):
        fechas = pd.to_datetime(limpio[col], errors="coerce")
        limpio[col] = fechas.dt.strftime("%Y-%m-%d").fillna("")

    for col in COLUMNAS_NUMERICAS & set(columnas):
        limpio[col] = a_numero(limpio[col]).round(2)

    for col in columnas:
        if col not in COLUMNAS_NUMERICAS:
            limpio[col] = texto_simple(limpio[col])
            if col in COLUMNAS_CLAVE:
                limpio[col] = limpio[col].str.replace(r"\.0$", "", regex=True)

    if {"Anio", "Mes"} <= set(columnas):
        limpio["Anio"] = limpio["Anio"].astype(int)
        limpio["Mes"] = limpio["Mes"].astype(int)

    return limpio.fillna("")


def huella(datos, columnas):
    """Firma del contenido para detectar que alguien más modificó la hoja."""
    base = para_escribir(datos, columnas).astype(str)
    return hashlib.md5(base.to_csv(index=False).encode("utf-8")).hexdigest()


# =============================================================
# LECTURA
# =============================================================
def leer_directo(nombre, columnas):
    datos = con_reintentos(lambda: conn.read(worksheet=nombre), nombre)
    if datos is None or datos.empty:
        return esqueleto(columnas)
    datos = datos.dropna(how="all")
    datos = datos.loc[:, ~datos.columns.astype(str).str.startswith("Unnamed")]
    for col in columnas:
        if col not in datos.columns:
            datos[col] = None
    datos = datos[columnas].reset_index(drop=True)
    # Las filas de relleno llegan como cadenas vacías, no como NaN
    vacias = datos.apply(lambda f: all(str(v).strip() in ("", "nan", "None") for v in f), axis=1)
    return datos[~vacias].reset_index(drop=True) if len(datos) else datos


@st.cache_data(ttl=300, show_spinner=False)
def leer_hoja(nombre, columnas):
    return leer_directo(nombre, tuple(columnas) and list(columnas))


# =============================================================
# BITÁCORA
# =============================================================
def usuario_actual():
    return st.session_state.get("usuario", "Sin identificar") or "Sin identificar"


def registrar_bitacora(filas):
    """Agrega renglones a la hoja de bitácora. Nunca detiene la operación principal."""
    if not filas:
        return
    try:
        actual = leer_directo(HOJA_BITACORA, COLS_BIT)
        nuevas = pd.DataFrame(filas, columns=COLS_BIT)
        completa = pd.concat([actual, nuevas], ignore_index=True)
        if len(completa) > MAX_BITACORA:
            completa = completa.tail(MAX_BITACORA).reset_index(drop=True)
        con_reintentos(lambda: conn.update(worksheet=HOJA_BITACORA, data=completa.fillna("")))
    except Exception as e:
        st.session_state["bitacora_error"] = str(e)


def _valor_legible(valor, campo):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if campo in COLUMNAS_NUMERICAS:
        try:
            return f"{float(valor):,.2f}"
        except (TypeError, ValueError):
            return str(valor)
    return str(valor).strip()


def diferencias(antes, despues, columnas, tabla, nota=""):
    """Compara dos versiones de una tabla y devuelve los renglones de bitácora."""
    marca = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    usuario = usuario_actual()
    filas = []

    if "ID" not in columnas:
        return filas

    a = para_escribir(antes, columnas).set_index("ID", drop=False)
    b = para_escribir(despues, columnas).set_index("ID", drop=False)

    a = a[~a.index.duplicated(keep="last")]
    b = b[~b.index.duplicated(keep="last")]

    def descripcion(fila):
        for campo in ("Descripcion", "Concepto", "Detalle"):
            if campo in fila.index and str(fila[campo]).strip():
                return str(fila[campo]).strip()
        return ""

    for rid in b.index.difference(a.index):
        fila = b.loc[rid]
        monto = next((c for c in ("Monto", "MontoProgramado") if c in fila.index), None)
        filas.append({
            "Timestamp": marca, "Usuario": usuario, "Accion": "ALTA", "Tabla": tabla,
            "RegistroID": rid, "Campo": "", "ValorAnterior": "",
            "ValorNuevo": _valor_legible(fila[monto], monto) if monto else "",
            "Detalle": f"{descripcion(fila)} {nota}".strip(),
        })

    for rid in a.index.difference(b.index):
        fila = a.loc[rid]
        monto = next((c for c in ("Monto", "MontoProgramado") if c in fila.index), None)
        filas.append({
            "Timestamp": marca, "Usuario": usuario, "Accion": "BAJA", "Tabla": tabla,
            "RegistroID": rid, "Campo": "",
            "ValorAnterior": _valor_legible(fila[monto], monto) if monto else "",
            "ValorNuevo": "", "Detalle": f"{descripcion(fila)} {nota}".strip(),
        })

    for rid in a.index.intersection(b.index):
        fila_a, fila_b = a.loc[rid], b.loc[rid]
        for campo in columnas:
            if campo == "ID":
                continue
            va, vb = _valor_legible(fila_a[campo], campo), _valor_legible(fila_b[campo], campo)
            if va != vb:
                filas.append({
                    "Timestamp": marca, "Usuario": usuario, "Accion": "CAMBIO", "Tabla": tabla,
                    "RegistroID": rid, "Campo": campo, "ValorAnterior": va, "ValorNuevo": vb,
                    "Detalle": f"{descripcion(fila_b)} {nota}".strip(),
                })

    return filas


def bitacora_evento(accion, tabla, registro_id="", detalle="", campo="", antes="", despues=""):
    return {
        "Timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Usuario": usuario_actual(), "Accion": accion, "Tabla": tabla,
        "RegistroID": registro_id, "Campo": campo,
        "ValorAnterior": antes, "ValorNuevo": despues, "Detalle": detalle,
    }


# =============================================================
# ESCRITURA CON CONTROL DE CONCURRENCIA
# =============================================================
def guardar_hoja(nombre, datos, columnas, antes=None, nota="", eventos_extra=None,
                 auditar=True, normalizador=None):
    """
    Escribe la hoja completa verificando antes que nadie más la haya movido.
    'normalizador' se aplica por igual a lo que tienes en memoria y a lo que hay en
    la hoja: sin eso, la comparación choca contra diferencias de formato que no son
    cambios reales (Sheets devuelve el 15 como '15.0').
    """
    try:
        destino = para_escribir(datos, columnas)
        remoto = leer_directo(nombre, columnas)

        if antes is not None:
            base = normalizador(antes) if normalizador else antes
            base_remoto = normalizador(remoto) if normalizador else remoto

            if huella(base, columnas) != huella(base_remoto, columnas):
                if not st.session_state.get("forzar_guardado"):
                    detalle = diferencias(base, base_remoto, columnas, nombre)[:3]
                    resumen = "; ".join(
                        f"{d['Accion']} {d['RegistroID']}"
                        + (f" · {d['Campo']}: «{d['ValorAnterior']}» → «{d['ValorNuevo']}»"
                           if d["Campo"] else "")
                        for d in detalle
                    ) or "no se pudo aislar la diferencia"
                    return False, (
                        f"🔒 La hoja «{nombre}» cambió desde que la cargaste. No se guardó nada "
                        f"para no borrar ese trabajo.\n\nDiferencias detectadas: {resumen}\n\n"
                        "Si fuiste tú desde otra pestaña, recarga y repite el cambio. Si esto se "
                        "repite sin que nadie más haya editado, actívalo en «Opciones avanzadas» "
                        "de la barra lateral."
                    )
                st.session_state["aviso_forzado"] = nombre

        destino = con_relleno(destino, columnas, len(remoto))
        con_reintentos(lambda: conn.update(worksheet=nombre, data=destino), nombre)
        st.cache_data.clear()

        if auditar:
            filas = []
            if antes is not None:
                base = normalizador(antes) if normalizador else antes
                filas = diferencias(base, para_escribir(datos, columnas), columnas, nombre, nota)
            if eventos_extra:
                filas.extend(eventos_extra)
            registrar_bitacora(filas)

        return True, f"✅ Guardado en «{nombre}»."
    except Exception as e:
        return False, f"⚠️ Error al guardar en «{nombre}»: {e}"


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

    for col in ["Tipo", "Categoria", "Descripcion", "RefID", "ID"]:
        datos[col] = texto_simple(datos[col])

    datos = datos.reset_index(drop=True)
    return reparar_ids(datos, COLS_MOV)


def solo_columnas_mov(datos):
    limpio = datos.drop(columns=["Fecha_DT", "Periodo_Label"], errors="ignore").copy()
    for col in COLS_MOV:
        if col not in limpio.columns:
            limpio[col] = None
    return limpio[COLS_MOV].reset_index(drop=True)


def posibles_duplicados(datos, fecha, tipo, categoria, monto, tolerancia_dias=0):
    if datos.empty:
        return esqueleto(COLS_MOV)
    objetivo = pd.Timestamp(fecha)
    cerca = (pd.to_datetime(datos["Fecha"], errors="coerce") - objetivo).abs() <= pd.Timedelta(days=tolerancia_dias)
    return datos[
        cerca
        & (datos["Tipo"] == tipo)
        & (datos["Categoria"] == categoria)
        & ((datos["Monto"] - float(monto)).abs() < 0.01)
    ]


# =============================================================
# MOTOR DE CALENDARIO
# =============================================================
def ocurrencias(frecuencia, dia_txt, inicio, expiracion, desde, hasta):
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


def equivalente_mensual(df_pre, referencia=None):
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
# COMPROMISOS
# =============================================================
def normalizar_compromisos(datos):
    if datos is None or datos.empty:
        return esqueleto(COLS_COMP)
    datos = datos.copy()
    datos["MontoProgramado"] = a_numero(datos["MontoProgramado"])
    datos["MontoPagado"] = a_numero(datos["MontoPagado"])
    for col in ["ID", "Categoria", "Concepto", "Estado", "MovimientoID", "Origen", "Notas"]:
        datos[col] = texto_simple(datos[col])
    datos["Estado"] = datos["Estado"].replace("", "Planeado")
    datos.loc[~datos["Estado"].isin(ESTADOS), "Estado"] = "Planeado"
    for col in ["FechaProgramada", "FechaPago"]:
        datos[col] = pd.to_datetime(datos[col], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    datos = datos[datos["FechaProgramada"] != ""].reset_index(drop=True)
    return reparar_ids(datos, COLS_COMP)


def normalizar_presupuestos(datos):
    if datos is None or datos.empty:
        return esqueleto(COLS_PRE)
    datos = datos.copy()
    datos["Monto"] = a_numero(datos["Monto"])
    datos["DiaPago"] = texto_dia(datos["DiaPago"])
    for col in ["ID", "Categoria", "Detalle", "Frecuencia"]:
        datos[col] = texto_simple(datos[col]).str.replace(r"\.0$", "", regex=True) \
            if col == "ID" else texto_simple(datos[col])
    datos = datos[datos["Detalle"] != ""].reset_index(drop=True)
    return reparar_ids(datos, COLS_PRE)


def normalizar_cobros(datos):
    if datos is None or datos.empty:
        return esqueleto(COLS_COB)
    datos = datos.copy()
    datos["Monto"] = a_numero(datos["Monto"])
    datos["DiaCobro"] = texto_dia(datos["DiaCobro"])
    datos["ID"] = texto_simple(datos["ID"]).str.replace(r"\.0$", "", regex=True)
    for col in ["Concepto", "Frecuencia"]:
        datos[col] = texto_simple(datos[col])
    datos = datos[datos["Concepto"] != ""].reset_index(drop=True)
    return reparar_ids(datos, COLS_COB)


def generar_compromisos(df_pre, df_comp, desde, hasta):
    existentes = set(zip(df_comp["Origen"].astype(str), df_comp["FechaProgramada"].astype(str)))
    nuevos, n = [], 0

    for _, row in df_pre.iterrows():
        regla_id = str(row.get("ID") or "").strip()
        detalle = str(row.get("Detalle") or "").strip()
        if regla_id == "" or detalle == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        for f in ocurrencias(row.get("Frecuencia"), row.get("DiaPago"), row.get("Inicio"),
                             row.get("Expiracion"), desde, hasta):
            clave = (regla_id, f.strftime("%Y-%m-%d"))
            if clave in existentes:
                continue
            existentes.add(clave)
            nuevos.append({
                "ID": nuevo_id(n), "Categoria": row.get("Categoria", ""), "Concepto": detalle,
                "MontoProgramado": monto, "FechaProgramada": f.strftime("%Y-%m-%d"),
                "Estado": "Planeado", "FechaPago": "", "MontoPagado": 0.0,
                "MovimientoID": "", "Origen": regla_id, "Notas": "",
            })
            n += 1

    return pd.DataFrame(nuevos, columns=COLS_COMP) if nuevos else esqueleto(COLS_COMP)


def marcar_como_pagado(df_mov, df_comp, comp_id, fecha_pago, monto_pagado, nota=""):
    fila = df_comp[df_comp["ID"] == comp_id]
    if fila.empty:
        return False, "No se encontró el compromiso."
    fila = fila.iloc[0]

    mov_id = nuevo_id()
    nuevo_mov = pd.DataFrame([{
        "ID": mov_id, "Fecha": fecha_pago.strftime("%Y-%m-%d"),
        "Anio": int(fecha_pago.year), "Mes": int(fecha_pago.month),
        "Tipo": "Gasto", "Categoria": fila["Categoria"], "Monto": float(monto_pagado),
        "Descripcion": f"{fila['Concepto']}{' · ' + nota if nota else ''}", "RefID": comp_id,
    }])

    ok_mov, msg_mov = guardar_hoja(
        HOJA_MOVIMIENTOS,
        pd.concat([solo_columnas_mov(df_mov), nuevo_mov], ignore_index=True),
        COLS_MOV, antes=solo_columnas_mov(df_mov),
        nota=f"generado por compromiso {comp_id}", normalizador=lambda d: solo_columnas_mov(normalizar_movimientos(d)),
    )
    if not ok_mov:
        return False, msg_mov

    actualizado = df_comp.copy()
    idx = actualizado["ID"] == comp_id
    actualizado.loc[idx, "Estado"] = "Pagado"
    actualizado.loc[idx, "FechaPago"] = fecha_pago.strftime("%Y-%m-%d")
    actualizado.loc[idx, "MontoPagado"] = float(monto_pagado)
    actualizado.loc[idx, "MovimientoID"] = mov_id
    if nota:
        actualizado.loc[idx, "Notas"] = nota

    ok_comp, msg_comp = guardar_hoja(
        HOJA_COMPROMISOS, actualizado, COLS_COMP, antes=df_comp, nota="pago registrado",
        normalizador=normalizar_compromisos,
        eventos_extra=[bitacora_evento("PAGO", HOJA_COMPROMISOS, comp_id,
                                       f"{fila['Categoria']} — {fila['Concepto']}",
                                       "Estado", fila["Estado"], "Pagado")],
    )
    if not ok_comp:
        return False, (
            f"El gasto SÍ se registró (ID {mov_id}) pero el compromiso no se actualizó. "
            f"Recarga y vuelve a intentarlo. {msg_comp}"
        )

    return True, f"✅ Pago registrado y agregado como gasto (ID {mov_id})."


def revertir_pago(df_mov, df_comp, comp_id):
    fila = df_comp[df_comp["ID"] == comp_id]
    if fila.empty:
        return False, "No se encontró el compromiso."
    mov_id = str(fila.iloc[0]["MovimientoID"]).strip()

    if mov_id:
        restante = df_mov[df_mov["ID"] != mov_id]
        ok_mov, msg_mov = guardar_hoja(
            HOJA_MOVIMIENTOS, solo_columnas_mov(restante), COLS_MOV,
            antes=solo_columnas_mov(df_mov), nota=f"reversión del compromiso {comp_id}",
            normalizador=lambda d: solo_columnas_mov(normalizar_movimientos(d)),)
        if not ok_mov:
            return False, msg_mov

    actualizado = df_comp.copy()
    idx = actualizado["ID"] == comp_id
    actualizado.loc[idx, ["Estado", "FechaPago", "MontoPagado", "MovimientoID"]] = \
        ["Comprometido", "", 0.0, ""]

    ok_comp, msg_comp = guardar_hoja(
        HOJA_COMPROMISOS, actualizado, COLS_COMP, antes=df_comp, nota="pago revertido",
        normalizador=normalizar_compromisos,
        eventos_extra=[bitacora_evento("REVERSION", HOJA_COMPROMISOS, comp_id,
                                       str(fila.iloc[0]["Concepto"]), "MovimientoID", mov_id, "")],
    )
    return (True, "↩️ Pago revertido.") if ok_comp else (False, msg_comp)


def cambiar_estado(df_comp, comp_id, estado):
    actualizado = df_comp.copy()
    actualizado.loc[actualizado["ID"] == comp_id, "Estado"] = estado
    return guardar_hoja(HOJA_COMPROMISOS, actualizado, COLS_COMP, antes=df_comp,
                        nota=f"estado → {estado}", normalizador=normalizar_compromisos,)


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
    df_pre = normalizar_presupuestos(leer_hoja(HOJA_PRESUPUESTOS, COLS_PRE))
except Exception as e:
    errores.append((HOJA_PRESUPUESTOS, e))
    df_pre = esqueleto(COLS_PRE)

try:
    df_cob = normalizar_cobros(leer_hoja(HOJA_COBROS, COLS_COB))
except Exception as e:
    errores.append((HOJA_COBROS, e))
    df_cob = esqueleto(COLS_COB)

try:
    df_comp = normalizar_compromisos(leer_hoja(HOJA_COMPROMISOS, COLS_COMP))
except Exception as e:
    errores.append((HOJA_COMPROMISOS, e))
    df_comp = esqueleto(COLS_COMP)

try:
    df_bit = leer_hoja(HOJA_BITACORA, COLS_BIT)
except Exception as e:
    errores.append((HOJA_BITACORA, e))
    df_bit = esqueleto(COLS_BIT)

for nombre, err in errores:
    msg_error(st, 
        f"No se pudo leer la pestaña **{nombre}**. Verifica que exista con ese nombre exacto "
        f"y que el archivo esté compartido como Editor con la cuenta de servicio.\n\n`{err}`"
    )

if st.session_state.get("aviso_forzado"):
    msg_warning(st, 
        f"⚠️ El último guardado en «{st.session_state.pop('aviso_forzado')}» se hizo con la "
        "protección de sobrescritura desactivada. Si alguien más estaba editando, sus cambios "
        "se perdieron. Revisa la bitácora."
    )

if st.session_state.get("bitacora_error"):
    msg_warning(st, 
        "⚠️ La última operación se guardó, pero no se pudo escribir en la bitácora: "
        f"`{st.session_state.pop('bitacora_error')}`"
    )

if not df_comp.empty and not df.empty:
    pagados = df_comp[(df_comp["Estado"] == "Pagado") & (df_comp["MovimientoID"] != "")]
    huerfanos = pagados[~pagados["MovimientoID"].isin(df["ID"])]
    if not huerfanos.empty:
        msg_warning(st, 
            f"🔗 {len(huerfanos)} compromiso(s) marcados como Pagado apuntan a un movimiento "
            f"que ya no existe: {', '.join(huerfanos['Concepto'].head(5))}. "
            "Reviértelos y regístralos de nuevo para que el gasto quede contado."
        )

st.session_state.setdefault("modo_revision", False)
st.session_state.setdefault("ignorar_alerta_cuadre", False)
st.session_state.setdefault("confirmar_duplicado", False)
st.session_state.setdefault("usuario", "")

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
            "Meses a sumar (acumulado flexible):", options=periodos, default=default,
            format_func=lambda p: f"{p.split(' - ')[0]} - {MESES_NOMBRES[int(p.split(' - ')[1])]}",
        )
    with col_f2:
        anios = sorted(df["Anio"].dropna().unique().tolist(), reverse=True)
        anio_seleccionado = st.selectbox("Año para el resumen anual:", options=anios, index=0)

    df_filtrado = df[df["Periodo_Label"].isin(meses_seleccionados)]
else:
    msg_info(st, "Aún no hay movimientos. Registra el primero desde la barra lateral.")
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
fondo_espana = float(df.loc[df["Categoria"] == "Fondo España", "Monto"].sum()) if not df.empty else 0.0


# =============================================================
# BARRA LATERAL
# =============================================================
st.sidebar.text_input("👤 ¿Quién está capturando?", key="usuario",
                      placeholder="Tu nombre o iniciales",
                      help="Se guarda en la bitácora junto a cada cambio.")
if not st.session_state.usuario.strip():
    msg_caption(st.sidebar, "Sin nombre, los cambios quedan como «Sin identificar».")

if st.sidebar.button("🔄 Recargar desde Google Sheets", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

with st.sidebar.expander("🛠️ Opciones avanzadas"):
    st.checkbox(
        "Ignorar protección de sobrescritura", key="forzar_guardado",
        help="Solo si el tablero te bloquea un guardado y estás seguro de que nadie más "
             "editó la hoja. Con esto activo, tu versión pisa lo que haya en la nube.",
    )
    if st.session_state.get("forzar_guardado"):
        msg_warning(st, "Protección desactivada. Vuelve a activarla al terminar.")

st.sidebar.divider()

if st.session_state.modo_revision:
    msg_warning(st.sidebar, "🔒 **Registro bloqueado**\n\nEstás en modo de revisión del historial.")
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

    duplicados = posibles_duplicados(df, fecha, tipo, categoria, monto) if monto > 0 else esqueleto(COLS_MOV)
    if not duplicados.empty:
        msg_warning(st.sidebar, 
            f"🔁 Ya existe un movimiento igual: {duplicados.iloc[0]['Fecha']} · "
            f"{duplicados.iloc[0]['Categoria']} · ${float(duplicados.iloc[0]['Monto']):,.2f}"
            + (f" ({duplicados.iloc[0]['Descripcion']})" if duplicados.iloc[0]["Descripcion"] else "")
        )
        st.session_state.confirmar_duplicado = st.sidebar.checkbox(
            "Sí, es otro gasto distinto. Registrarlo igual.", key="chk_dup")
    else:
        st.session_state.confirmar_duplicado = False

    if st.sidebar.button("Guardar Movimiento", type="primary", use_container_width=True):
        if not (2024 <= fecha.year <= 2035):
            msg_error(st.sidebar, "⚠️ El año está fuera del rango válido.")
        elif monto <= 0:
            msg_error(st.sidebar, "⚠️ El monto debe ser mayor a 0.")
        elif not duplicados.empty and not st.session_state.confirmar_duplicado:
            msg_error(st.sidebar, "⚠️ Posible duplicado. Marca la casilla si de verdad quieres registrarlo.")
        else:
            nuevo = pd.DataFrame([{
                "ID": nuevo_id(), "Fecha": fecha.strftime("%Y-%m-%d"),
                "Anio": int(fecha.year), "Mes": int(fecha.month), "Tipo": tipo,
                "Categoria": categoria, "Monto": float(monto),
                "Descripcion": descripcion, "RefID": "",
            }])
            exito, mensaje = guardar_hoja(
                HOJA_MOVIMIENTOS,
                pd.concat([solo_columnas_mov(df), nuevo], ignore_index=True),
                COLS_MOV, antes=solo_columnas_mov(df), nota="captura manual",
                normalizador=lambda d: solo_columnas_mov(normalizar_movimientos(d)),
            )
            if exito:
                st.session_state.modo_revision = False
                st.session_state.ignorar_alerta_cuadre = False
                st.session_state.confirmar_duplicado = False
                msg_success(st.sidebar, mensaje)
                st.rerun()
            else:
                msg_error(st.sidebar, mensaje)

st.sidebar.divider()
msg_caption(st.sidebar, 
    f"Movimientos: {len(df)} · Partidas: {len(df_pre)} · "
    f"Cobros: {len(df_cob)} · Compromisos: {len(df_comp)} · Bitácora: {len(df_bit)}"
)


# =============================================================
# ALERTA DE CUADRE
# =============================================================
if alerta_cuadre_activa and not st.session_state.ignorar_alerta_cuadre:
    msg_error(st, 
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
# COMPONENTES VISUALES
# =============================================================
def barra_presupuesto(nombre, real, limite):
    pct = 0.0 if limite <= 0 else real / limite
    if pct < 0.75:
        color, etiqueta = "#2e9e5b", "En control"
    elif pct <= 1.0:
        color, etiqueta = "#e0a106", "Cerca del límite"
    else:
        color, etiqueta = "#d1443c", "Excedido"
    ancho = min(pct, 1.0) * 100
    st.markdown(
        f"""
        <div style="margin-bottom:16px;">
          <div style="display:flex;justify-content:space-between;font-size:14px;margin-bottom:4px;">
            <span><b>{nombre}</b> <span style="color:#666;">· {etiqueta}</span></span>
            <span>&#36;{real:,.2f} / &#36;{limite:,.2f} <b>({pct*100:.0f}%)</b></span>
          </div>
          <div style="background:#e9ecef;border-radius:6px;height:14px;overflow:hidden;">
            <div style="width:{ancho}%;background:{color};height:100%;"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def eventos_semaforo(df_pre, df_cob, df_comp, desde, hasta):
    eventos, materializados = [], set()

    if not df_comp.empty:
        for _, row in df_comp.iterrows():
            f = _a_fecha(row["FechaProgramada"])
            if not f:
                continue
            origen = str(row.get("Origen") or "").strip()
            if origen:
                materializados.add((origen, f))
            if row["Estado"] in ("Pagado", "Cancelado"):
                continue
            if desde <= f <= hasta:
                eventos.append({
                    "Fecha": f, "Tipo": "Pago",
                    "Concepto": f"{row['Categoria']} — {row['Concepto']}".strip(" —"),
                    "Monto": float(row["MontoProgramado"]), "Fuente": row["Estado"],
                })

    for _, row in df_pre.iterrows():
        regla_id = str(row.get("ID") or "").strip()
        detalle = str(row.get("Detalle") or "").strip()
        if detalle == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        for f in ocurrencias(row.get("Frecuencia"), row.get("DiaPago"), row.get("Inicio"),
                             row.get("Expiracion"), desde, hasta):
            if (regla_id, f) in materializados:
                continue
            eventos.append({
                "Fecha": f, "Tipo": "Pago",
                "Concepto": f"{row.get('Categoria', '')} — {detalle}".strip(" —"),
                "Monto": monto, "Fuente": "Proyectado",
            })

    for _, row in df_cob.iterrows():
        if str(row.get("Concepto") or "").strip() == "":
            continue
        monto = float(row.get("Monto") or 0.0)
        for f in ocurrencias(row.get("Frecuencia"), row.get("DiaCobro"), row.get("Inicio"),
                             row.get("Expiracion"), desde, hasta):
            eventos.append({
                "Fecha": f, "Tipo": "Cobro", "Concepto": str(row.get("Concepto") or ""),
                "Monto": monto, "Fuente": "Programado",
            })

    if not eventos:
        return pd.DataFrame(columns=["Fecha", "Tipo", "Concepto", "Monto", "Fuente"])

    tabla = pd.DataFrame(eventos)
    tabla["_orden"] = tabla["Tipo"].map({"Cobro": 0, "Pago": 1})
    return tabla.sort_values(["Fecha", "_orden", "Concepto"]).drop(columns="_orden").reset_index(drop=True)


# =============================================================
# PESTAÑAS
# =============================================================
(tab_mes, tab_anual, tab_pre, tab_comp,
 tab_semaforo, tab_meta, tab_bit, tab_admin) = st.tabs(
    ["📊 Resumen de Periodo", "📅 Resumen del Año", "💰 Presupuestos y Calendario",
     "🧾 Compromisos y Pagos", "🚦 Semáforo", "🎯 Meta España",
     "🗂️ Bitácora", "⚙️ Administrar Historial"]
)

with tab_mes:
    st.header("Resumen del periodo seleccionado")

    tasa_ahorro = (total_ahorro_f / total_ingresos_f * 100) if total_ingresos_f > 0 else 0.0
    faltante_meta = max(META_ESPANA - fondo_espana, 0.0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ingresos totales", f"${total_ingresos_f:,.2f}")
    c2.metric("Gastos totales", f"${total_gastos_f:,.2f}")
    c3.metric("Margen disponible", f"${margen_disponible:,.2f}", delta=f"{tasa_ahorro:.1f}% tasa de ahorro")
    c4.metric("Fondo España", f"${fondo_espana:,.2f}", delta=f"Faltan ${faltante_meta:,.2f}")

    avance = 0.0 if META_ESPANA <= 0 else min(max(fondo_espana / META_ESPANA, 0.0), 1.0)
    st.progress(avance, text=f"Avance hacia la meta: {avance * 100:.1f}%")

    hoy = date.today()
    linea_corta = eventos_semaforo(df_pre, df_cob, df_comp, hoy, hoy + timedelta(days=15))
    if not linea_corta.empty:
        saldo_tmp = margen_disponible
        for _, ev in linea_corta.iterrows():
            if ev["Tipo"] == "Cobro":
                saldo_tmp += ev["Monto"]
            else:
                if saldo_tmp < ev["Monto"]:
                    msg_error(st, 
                        f"🚦 **Ojo en los próximos 15 días**: «{ev['Concepto']}» por "
                        f"${ev['Monto']:,.2f} el {ev['Fecha'].strftime('%d/%m')} y tu saldo "
                        f"proyectado sería de ${max(saldo_tmp, 0):,.2f}. Revisa el Semáforo."
                    )
                    break
                saldo_tmp -= ev["Monto"]

    st.markdown("---")
    st.subheader("🎯 Presupuesto vs. real por categoría")
    if not presupuestos_activos:
        msg_info(st, "Configura tus partidas en «Presupuestos y Calendario» para ver este comparativo.")
    else:
        gastos_reales = (
            df_filtrado[df_filtrado["Tipo"] == "Gasto"].groupby("Categoria")["Monto"].sum()
            if not df_filtrado.empty else pd.Series(dtype=float)
        )
        b1, b2 = st.columns(2)
        for i, (cat, lim) in enumerate(sorted(presupuestos_activos.items())):
            with (b1 if i % 2 == 0 else b2):
                barra_presupuesto(cat, float(gastos_reales.get(cat, 0.0)), lim)

        total_lim = sum(presupuestos_activos.values())
        total_real = float(gastos_reales.sum()) if len(gastos_reales) else 0.0
        msg_caption(st, 
            f"Total presupuestado del mes: **${total_lim:,.2f}** · "
            f"Ejercido: **${total_real:,.2f}** · Disponible: **${total_lim - total_real:,.2f}**"
        )
        sin_presupuesto = [c for c in gastos_reales.index if c not in presupuestos_activos]
        if sin_presupuesto:
            msg_warning(st, f"Gastaste en categorías sin presupuesto asignado: **{', '.join(sin_presupuesto)}**")

    st.markdown("---")
    st.subheader("📈 Gráficas del periodo")
    if df_filtrado.empty:
        msg_info(st, "Selecciona un periodo con movimientos.")
    else:
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Gastos por categoría**")
            df_gastos = df_filtrado[df_filtrado["Tipo"] == "Gasto"]
            if df_gastos.empty:
                msg_info(st, "Sin gastos en este periodo.")
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

    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Ingresos anuales", f"${ingresos_anio:,.2f}")
    a2.metric("Gastos anuales", f"${gastos_anio:,.2f}")
    a3.metric("Ahorro anual", f"${ahorro_anio:,.2f}")
    a4.metric("Tasa de ahorro", f"{tasa_anio:.1f}%")
    a5.metric("Faltante meta", f"${max(META_ESPANA - fondo_espana, 0.0):,.2f}")

    st.markdown("---")
    if df_anual.empty:
        msg_info(st, f"Sin movimientos en {anio_seleccionado}.")
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
    msg_caption(st, 
        "Frecuencias · **Mensual**: día(s) del mes, ej. `15,30` o `Fin`. "
        "**Semanal**: nombre del día, ej. `Sabado`. "
        "**Catorcenal**: cada 14 días desde *Inicio*. **Unica**: una fecha `AAAA-MM-DD`."
    )

    st.subheader("💵 Días de cobro")
    cobros_edit = st.data_editor(
        df_cob, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "ID": st.column_config.TextColumn("ID", disabled=True),
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
        exito, mensaje = guardar_hoja(HOJA_COBROS, rellenar_ids(limpio), COLS_COB,
                                      antes=df_cob, nota="edición de cobros",
                                      normalizador=normalizar_cobros,)
        if exito:
            msg_success(st, mensaje)
            st.rerun()
        else:
            msg_error(st, mensaje)

    st.divider()
    st.subheader("📂 Partidas de gasto por categoría")

    cs1, cs2 = st.columns([3, 1])
    with cs1:
        cat_sel = st.selectbox("Categoría a revisar o editar:", CATEGORIAS_GASTO)
    with cs2:
        st.write("")
        st.write("")
        if st.button("🔄 Recargar hoja", use_container_width=True, key="recargar_pre"):
            st.cache_data.clear()
            st.rerun()

    msg_caption(st, "Para borrar una partida, selecciona su renglón con la casilla de la "
               "izquierda, presiona Suprimir y luego guarda.")
    df_cat = df_pre[df_pre["Categoria"] == cat_sel].reset_index(drop=True)

    pre_edit = st.data_editor(
        df_cat[["ID", "Detalle", "Monto", "Frecuencia", "DiaPago", "Inicio", "Expiracion"]],
        num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "ID": st.column_config.TextColumn("ID", disabled=True),
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
            HOJA_PRESUPUESTOS, pd.concat([resto, nuevas], ignore_index=True), COLS_PRE,
            antes=df_pre, nota=f"edición de partidas · {cat_sel}",
            normalizador=normalizar_presupuestos,)
        if exito:
            msg_success(st, mensaje)
            st.rerun()
        else:
            msg_error(st, mensaje)

    st.divider()
    st.subheader("📋 Equivalente mensual activo")
    if presupuestos_activos:
        st.dataframe(
            pd.DataFrame([
                {"Categoría": c, "Total del mes": f"${v:,.2f}", "Apartado semanal (÷4)": f"${v/4:,.2f}"}
                for c, v in sorted(presupuestos_activos.items())
            ]),
            use_container_width=True, hide_index=True,
        )
        total_mensual = sum(presupuestos_activos.values())
        t1, t2 = st.columns(2)
        t1.metric("💰 Presupuesto mensual global", f"${total_mensual:,.2f}")
        t2.metric("📅 Total semanal a apartar", f"${total_mensual/4:,.2f}")
    else:
        msg_info(st, "No hay partidas activas configuradas para este mes.")

    if expirados:
        msg_success(st, f"✅ Ya expiraron y no se cuentan: **{', '.join(sorted(set(expirados)))}**")

with tab_comp:
    st.header("🧾 Compromisos y pagos")
    msg_caption(st, 
        "Ciclo: **Planeado** → **Comprometido** → **Pagado** (se registra solo como gasto). "
        "**Cancelado** lo saca del semáforo sin borrarlo."
    )

    hoy = date.today()
    fin_mes = date(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])

    with st.expander("⚙️ Generar compromisos desde el calendario", expanded=df_comp.empty):
        gc1, gc2, gc3 = st.columns(3)
        with gc1:
            gen_desde = st.date_input("Desde", hoy, key="gen_desde")
        with gc2:
            gen_hasta = st.date_input("Hasta", fin_mes + timedelta(days=31), key="gen_hasta")
        with gc3:
            st.write("")
            st.write("")
            if st.button("⚡ Generar", type="primary", use_container_width=True):
                nuevos = generar_compromisos(df_pre, df_comp, gen_desde, gen_hasta)
                if nuevos.empty:
                    msg_info(st, "No hay compromisos nuevos que generar en ese rango.")
                else:
                    exito, mensaje = guardar_hoja(
                        HOJA_COMPROMISOS, pd.concat([df_comp, nuevos], ignore_index=True),
                        COLS_COMP, antes=df_comp, normalizador=normalizar_compromisos,
                        nota=f"generación automática {gen_desde}→{gen_hasta}")
                    if exito:
                        msg_success(st, f"Se generaron {len(nuevos)} compromiso(s).")
                        st.rerun()
                    else:
                        msg_error(st, mensaje)

    pendientes = df_comp[df_comp["Estado"].isin(["Planeado", "Comprometido"])].copy()
    if not pendientes.empty:
        pendientes["_f"] = pd.to_datetime(pendientes["FechaProgramada"])
        pendientes = pendientes.sort_values("_f")
        vencidos = pendientes[pendientes["_f"].dt.date < hoy]
        proximos_7 = pendientes[(pendientes["_f"].dt.date >= hoy) &
                                (pendientes["_f"].dt.date <= hoy + timedelta(days=7))]
    else:
        vencidos = proximos_7 = esqueleto(COLS_COMP)

    pagado_mes = df_comp[(df_comp["Estado"] == "Pagado") &
                         (df_comp["FechaPago"].str[:7] == hoy.strftime("%Y-%m"))]["MontoPagado"].sum()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Pendientes", len(pendientes))
    k2.metric("Vencidos", len(vencidos),
              delta=f"${vencidos['MontoProgramado'].sum():,.2f}" if len(vencidos) else None)
    k3.metric("Próximos 7 días", len(proximos_7),
              delta=f"${proximos_7['MontoProgramado'].sum():,.2f}" if len(proximos_7) else None)
    k4.metric("Pagado este mes", f"${pagado_mes:,.2f}")

    if len(vencidos):
        msg_error(st, 
            f"⏰ Tienes {len(vencidos)} compromiso(s) vencidos sin marcar como pagados: "
            + ", ".join(f"{r['Concepto']} ({r['FechaProgramada']})" for _, r in vencidos.head(4).iterrows())
        )

    st.divider()
    st.subheader("✅ Registrar un pago")

    if pendientes.empty:
        msg_info(st, "No hay compromisos pendientes. Genera algunos desde el calendario o agrégalos abajo.")
    else:
        opciones = {
            f"{r['FechaProgramada']} · {r['Categoria']} — {r['Concepto']} · ${float(r['MontoProgramado']):,.2f}": r["ID"]
            for _, r in pendientes.iterrows()
        }
        etiqueta = st.selectbox("Compromiso:", list(opciones.keys()))
        comp_id = opciones[etiqueta]
        fila_sel = df_comp[df_comp["ID"] == comp_id].iloc[0]

        p1, p2, p3 = st.columns(3)
        with p1:
            fecha_pago = st.date_input("Fecha real del pago", hoy, key="fp")
        with p2:
            monto_pagado = st.number_input("Monto real pagado ($)", min_value=0.0,
                                           value=float(fila_sel["MontoProgramado"]), step=100.0, key="mp")
        with p3:
            nota = st.text_input("Nota (opcional)", key="np")

        diferencia = monto_pagado - float(fila_sel["MontoProgramado"])
        if abs(diferencia) > 0.01:
            msg_warning(st, 
                f"El monto real difiere del programado en **${diferencia:,.2f}** "
                f"({'de más' if diferencia > 0 else 'de menos'}). Se registrará el monto real."
            )

        # Sin verificación de duplicados: el gasto que se va a crear lo genera este
        # mismo pago, así que siempre se parecería a sí mismo. La verificación sigue
        # activa en la captura manual de la barra lateral, que es donde sí aplica.

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("💸 Marcar como PAGADO", type="primary", use_container_width=True):
                exito, mensaje = marcar_como_pagado(df, df_comp, comp_id, fecha_pago, monto_pagado, nota)
                if exito:
                    msg_success(st, mensaje)
                    st.rerun()
                else:
                    msg_error(st, mensaje)
        with b2:
            if st.button("🟡 Marcar como Comprometido", use_container_width=True):
                exito, mensaje = cambiar_estado(df_comp, comp_id, "Comprometido")
                if exito:
                    msg_success(st, mensaje)
                    st.rerun()
                else:
                    msg_error(st, mensaje)
        with b3:
            if st.button("⚫ Cancelar compromiso", use_container_width=True):
                exito, mensaje = cambiar_estado(df_comp, comp_id, "Cancelado")
                if exito:
                    msg_success(st, mensaje)
                    st.rerun()
                else:
                    msg_error(st, mensaje)

    st.divider()
    st.subheader("↩️ Deshacer un pago")
    pagados = df_comp[df_comp["Estado"] == "Pagado"]
    if pagados.empty:
        msg_caption(st, "Todavía no hay pagos registrados.")
    else:
        op_pag = {
            f"{r['FechaPago']} · {r['Concepto']} · ${float(r['MontoPagado']):,.2f}": r["ID"]
            for _, r in pagados.sort_values("FechaPago", ascending=False).head(30).iterrows()
        }
        etq = st.selectbox("Pago a revertir:", list(op_pag.keys()), key="rev")
        msg_caption(st, "Borra el gasto ligado y regresa el compromiso a Comprometido. Queda en la bitácora.")
        if st.button("↩️ Revertir este pago"):
            exito, mensaje = revertir_pago(df, df_comp, op_pag[etq])
            if exito:
                msg_success(st, mensaje)
                st.rerun()
            else:
                msg_error(st, mensaje)

    st.divider()
    st.subheader("📖 Todos los compromisos")
    filtro = st.multiselect("Filtrar por estado:", ESTADOS, default=["Planeado", "Comprometido"])
    vista = df_comp[df_comp["Estado"].isin(filtro)].copy() if filtro else df_comp.copy()
    if vista.empty:
        msg_caption(st, "Sin compromisos con ese filtro.")
    else:
        vista = vista.sort_values("FechaProgramada")
        vista["Estado"] = vista["Estado"].map(ESTADO_ICONO).fillna(vista["Estado"])
        st.dataframe(
            vista[["FechaProgramada", "Categoria", "Concepto", "MontoProgramado",
                   "Estado", "FechaPago", "MontoPagado", "Notas"]].rename(columns={
                "FechaProgramada": "Programado", "MontoProgramado": "Monto plan.",
                "FechaPago": "Pagado el", "MontoPagado": "Monto real"}),
            use_container_width=True, hide_index=True,
        )

    with st.expander("➕ Agregar o editar compromisos a mano"):
        comp_edit = st.data_editor(
            df_comp, num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "ID": st.column_config.TextColumn("ID", disabled=True),
                "Categoria": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS_GASTO),
                "Concepto": st.column_config.TextColumn("Concepto", required=True),
                "MontoProgramado": st.column_config.NumberColumn("Monto plan. ($)", format="$%.2f", step=100.0),
                "FechaProgramada": st.column_config.TextColumn("Programado (AAAA-MM-DD)"),
                "Estado": st.column_config.SelectboxColumn("Estado", options=ESTADOS),
                "FechaPago": st.column_config.TextColumn("Pagado el"),
                "MontoPagado": st.column_config.NumberColumn("Monto real ($)", format="$%.2f"),
                "MovimientoID": st.column_config.TextColumn("Mov. ligado", disabled=True),
                "Origen": st.column_config.TextColumn("Regla origen", disabled=True),
                "Notas": st.column_config.TextColumn("Notas"),
            },
            key="editor_comp",
        )
        msg_caption(st, "Marcar 'Pagado' aquí NO crea el gasto. Usa el botón de arriba para eso.")
        if st.button("💾 Guardar compromisos"):
            limpio = comp_edit[comp_edit["Concepto"].astype(str).str.strip() != ""].copy()
            exito, mensaje = guardar_hoja(HOJA_COMPROMISOS, rellenar_ids(limpio), COLS_COMP,
                                          antes=df_comp, nota="edición manual",
                                          normalizador=normalizar_compromisos,)
            if exito:
                msg_success(st, mensaje)
                st.rerun()
            else:
                msg_error(st, mensaje)

with tab_semaforo:
    st.header("🚦 Semáforo de flujo")

    hoy = date.today()
    etiqueta_mes = hoy.strftime("%Y - %m")
    mes_curso = df[df["Periodo_Label"] == etiqueta_mes] if not df.empty else esqueleto(COLS_MOV)

    ing_mes = suma_por_tipo(mes_curso, "Ingreso")
    gas_mes = suma_por_tipo(mes_curso, "Gasto")
    aho_mes = suma_por_tipo(mes_curso, "Ahorro")
    saldo_auto = ing_mes - gas_mes - aho_mes

    s1, s2 = st.columns([1, 2])
    with s1:
        dias_vista = st.slider("Días a proyectar", 7, 90, 30)
    with s2:
        base_saldo = st.radio(
            "Saldo disponible de hoy:",
            ["Calcularlo automáticamente", "Escribirlo yo"],
            horizontal=True,
            help="El automático toma lo que te queda del mes en curso: "
                 "ingresos − gastos − ahorro.",
        )

    if base_saldo == "Calcularlo automáticamente":
        saldo_inicial = saldo_auto
        c_s1, c_s2 = st.columns([1, 2])
        c_s1.metric(f"Disponible de {MESES_NOMBRES[hoy.month]}", f"${saldo_inicial:,.2f}")
        with c_s2:
            st.write("")
            msg_caption(st, 
                f"Ingresos ${ing_mes:,.2f} − gastos ${gas_mes:,.2f} − "
                f"ahorro ${aho_mes:,.2f} = **${saldo_auto:,.2f}**. "
                "Se calcula sobre el mes en curso, no sobre los periodos que elegiste arriba."
            )
        if mes_curso.empty:
            msg_info(st, f"Todavía no hay movimientos registrados en {MESES_NOMBRES[hoy.month]}, "
                    "así que el disponible arranca en cero.")
    else:
        saldo_inicial = st.number_input(
            "Saldo disponible hoy ($)", value=float(round(saldo_auto, 2)), step=100.0,
            help="El dinero real que tienes en la cuenta ahora mismo.",
        )

    linea = eventos_semaforo(df_pre, df_cob, df_comp, hoy, hoy + timedelta(days=dias_vista))

    if linea.empty:
        msg_info(st, "No hay cobros ni pagos pendientes en este rango.")
    else:
        saldo = float(saldo_inicial)
        filas, faltante_total, primer_problema = [], 0.0, None

        for _, ev in linea.iterrows():
            if ev["Tipo"] == "Cobro":
                saldo += ev["Monto"]
                estado, detalle = "💵 ENTRA DINERO", f"Saldo tras el cobro: ${saldo:,.2f}"
            elif saldo >= ev["Monto"]:
                saldo -= ev["Monto"]
                estado, detalle = "✅ ALCANZA", f"Te quedan ${saldo:,.2f}"
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
                "Movimiento": ev["Concepto"], "Origen": ev["Fuente"],
                "Monto": ("+" if ev["Tipo"] == "Cobro" else "−") + f"${ev['Monto']:,.2f}",
                "Estado": estado, "Detalle": detalle,
            })

        if primer_problema:
            f, concepto, monto_p, saldo_p, faltan_p = primer_problema
            msg_error(st, 
                f"⚠️ **Peligro el {f.strftime('%d/%m/%Y')}**: «{concepto}» por **${monto_p:,.2f}** "
                f"y tu saldo proyectado sería de **${saldo_p:,.2f}**. Faltan **${faltan_p:,.2f}**."
            )
            if faltante_total > faltan_p + 0.01:
                msg_warning(st, f"Faltante acumulado en todo el rango: **${faltante_total:,.2f}**.")
        else:
            msg_success(st, f"✅ Todos los pagos de los próximos {dias_vista} días están cubiertos.")

        m1, m2, m3 = st.columns(3)
        m1.metric("Saldo proyectado al final", f"${saldo:,.2f}")
        m2.metric("Total a pagar", f"${linea.loc[linea['Tipo']=='Pago','Monto'].sum():,.2f}")
        m3.metric("Total a cobrar", f"${linea.loc[linea['Tipo']=='Cobro','Monto'].sum():,.2f}")

        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
        msg_caption(st, "Origen: *Planeado/Comprometido* son compromisos reales; *Proyectado* viene de la regla.")

with tab_meta:
    st.header("🎯 Proyección de la meta")

    fecha_objetivo = st.date_input("Fecha objetivo", FECHA_META_DEFAULT)
    faltante = max(META_ESPANA - fondo_espana, 0.0)
    ahorros = df[df["Tipo"] == "Ahorro"].copy() if not df.empty else esqueleto(COLS_MOV)

    if ahorros.empty:
        msg_info(st, "Registra al menos un movimiento de tipo Ahorro para calcular la proyección.")
    else:
        por_mes = ahorros.groupby("Periodo_Label")["Monto"].sum().sort_index()
        hoy = date.today()

        ultimos = []
        for i in range(6):
            mes, anio = hoy.month - i, hoy.year
            while mes <= 0:
                mes += 12
                anio -= 1
            ultimos.append(f"{anio} - {mes:02d}")
        ritmo_6m = float(sum(por_mes.get(p, 0.0) for p in ultimos) / 6)
        ritmo_hist = float(por_mes.mean())

        meses_restantes = max((fecha_objetivo.year - hoy.year) * 12 +
                              (fecha_objetivo.month - hoy.month), 1)
        requerido = faltante / meses_restantes
        brecha = requerido - ritmo_6m

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Ahorrado", f"${fondo_espana:,.2f}",
                  delta=f"{fondo_espana/META_ESPANA*100:.1f}% de la meta")
        r2.metric("Ritmo últimos 6 meses", f"${ritmo_6m:,.2f}/mes",
                  delta=f"Histórico ${ritmo_hist:,.2f}")
        r3.metric("Necesario por mes", f"${requerido:,.2f}", delta=f"{meses_restantes} meses restantes")
        r4.metric("Brecha mensual", f"${abs(brecha):,.2f}",
                  delta="Vas sobrado" if brecha <= 0 else "Te falta ese extra",
                  delta_color="normal" if brecha <= 0 else "inverse")

        if ritmo_6m > 0:
            llegada = hoy + timedelta(days=int((faltante / ritmo_6m) * 30.44))
            if llegada <= fecha_objetivo:
                msg_success(st, 
                    f"✅ A tu ritmo actual llegas a los ${META_ESPANA:,.0f} alrededor de "
                    f"**{MESES_NOMBRES[llegada.month]} {llegada.year}**, "
                    f"{(fecha_objetivo - llegada).days} días antes del objetivo."
                )
            else:
                msg_error(st, 
                    f"⚠️ A tu ritmo actual llegarías hasta **{MESES_NOMBRES[llegada.month]} "
                    f"{llegada.year}**, {(llegada - fecha_objetivo).days} días tarde. Necesitas "
                    f"subir el ahorro **${brecha:,.2f} al mes** para cumplir en la fecha objetivo."
                )
        else:
            msg_error(st, "Tu ritmo de ahorro de los últimos 6 meses es cero. La meta no avanza.")

        st.markdown("---")
        st.subheader("Trayectoria proyectada")

        etiquetas, real_linea, ritmo_linea, req_linea = [], [], [], []
        acumulado = 0.0
        for p in por_mes.index:
            acumulado += float(por_mes[p])
            anio, mes = p.split(" - ")
            etiquetas.append(f"{MESES_NOMBRES[int(mes)][:3]} {anio[2:]}")
            real_linea.append(acumulado)
            ritmo_linea.append(None)
            req_linea.append(None)

        base = acumulado
        for i in range(1, meses_restantes + 1):
            mes = hoy.month + i
            anio = hoy.year + (mes - 1) // 12
            mes = (mes - 1) % 12 + 1
            etiquetas.append(f"{MESES_NOMBRES[mes][:3]} {str(anio)[2:]}")
            real_linea.append(None)
            ritmo_linea.append(base + ritmo_6m * i)
            req_linea.append(base + requerido * i)

        grafica = pd.DataFrame(
            {"Ahorro real": real_linea, "A tu ritmo": ritmo_linea, "Necesario": req_linea},
            index=etiquetas)
        grafica["Meta"] = META_ESPANA
        st.line_chart(grafica)
        msg_caption(st, "«A tu ritmo» usa el promedio de los últimos 6 meses. «Necesario» es la recta a la meta.")

with tab_bit:
    st.header("🗂️ Bitácora de cambios")
    msg_caption(st, "Cada alta, cambio, baja, pago y reversión queda registrada con quién y cuándo.")

    if df_bit.empty:
        msg_info(st, "Todavía no hay movimientos registrados en la bitácora.")
    else:
        bit = df_bit.copy()
        bit["_ts"] = pd.to_datetime(bit["Timestamp"], errors="coerce")
        bit = bit.sort_values("_ts", ascending=False)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            usuarios = sorted(u for u in bit["Usuario"].dropna().unique() if str(u).strip())
            f_usuario = st.multiselect("Usuario", usuarios)
        with f2:
            f_tabla = st.multiselect("Tabla", sorted(bit["Tabla"].dropna().unique()))
        with f3:
            f_accion = st.multiselect("Acción", sorted(bit["Accion"].dropna().unique()))
        with f4:
            dias_bit = st.selectbox("Periodo", ["Últimos 7 días", "Últimos 30 días",
                                                "Últimos 90 días", "Todo"], index=1)

        if dias_bit != "Todo":
            corte = pd.Timestamp.now() - pd.Timedelta(days=int(dias_bit.split()[1]))
            bit = bit[bit["_ts"] >= corte]
        if f_usuario:
            bit = bit[bit["Usuario"].isin(f_usuario)]
        if f_tabla:
            bit = bit[bit["Tabla"].isin(f_tabla)]
        if f_accion:
            bit = bit[bit["Accion"].isin(f_accion)]

        busqueda = st.text_input("🔎 Buscar en concepto, campo o valores", "")
        if busqueda.strip():
            patron = busqueda.strip()
            mascara = (
                bit["Detalle"].astype(str).str.contains(patron, case=False, na=False)
                | bit["Campo"].astype(str).str.contains(patron, case=False, na=False)
                | bit["ValorAnterior"].astype(str).str.contains(patron, case=False, na=False)
                | bit["ValorNuevo"].astype(str).str.contains(patron, case=False, na=False)
                | bit["RegistroID"].astype(str).str.contains(patron, case=False, na=False)
            )
            bit = bit[mascara]

        k1, k2, k3 = st.columns(3)
        k1.metric("Registros mostrados", len(bit))
        k2.metric("Usuarios distintos", bit["Usuario"].nunique())
        k3.metric("Último cambio",
                  bit["_ts"].max().strftime("%d/%m %H:%M") if len(bit) and pd.notna(bit["_ts"].max()) else "—")

        if bit.empty:
            msg_caption(st, "Ningún cambio coincide con esos filtros.")
        else:
            st.dataframe(
                bit[COLS_BIT].rename(columns={
                    "Timestamp": "Cuándo", "RegistroID": "Registro",
                    "ValorAnterior": "Antes", "ValorNuevo": "Después"}),
                use_container_width=True, hide_index=True, height=420,
            )
            st.download_button(
                "⬇️ Descargar como CSV",
                bit[COLS_BIT].to_csv(index=False).encode("utf-8"),
                file_name=f"bitacora_{date.today():%Y%m%d}.csv",
                mime="text/csv",
            )

        st.divider()
        st.subheader("📊 Actividad por tabla")
        resumen = df_bit.groupby(["Tabla", "Accion"]).size().unstack(fill_value=0)
        if not resumen.empty:
            st.bar_chart(resumen)

with tab_admin:
    st.header("⚙️ Administrar historial de movimientos")
    if df.empty:
        msg_info(st, "No hay movimientos que administrar.")
    else:
        ligados = int((df["RefID"] != "").sum())
        if ligados:
            msg_caption(st, f"🔗 {ligados} movimiento(s) provienen de un compromiso. "
                       "Si borras uno aquí, revierte también el compromiso para no descuadrar.")

        df_admin = st.data_editor(
            solo_columnas_mov(df), num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "ID": st.column_config.TextColumn("ID", disabled=True),
                "Fecha": st.column_config.TextColumn("Fecha (AAAA-MM-DD)"),
                "Tipo": st.column_config.SelectboxColumn("Tipo", options=["Gasto", "Ingreso", "Ahorro"]),
                "Monto": st.column_config.NumberColumn("Monto ($)", format="$%.2f", step=100.0),
                "RefID": st.column_config.TextColumn("Compromiso", disabled=True),
            },
            key="editor_financiero",
        )
        b1, b2 = st.columns([1, 3])
        with b1:
            if st.button("💾 Guardar cambios", type="primary", use_container_width=True):
                exito, mensaje = guardar_hoja(
                    HOJA_MOVIMIENTOS, solo_columnas_mov(normalizar_movimientos(df_admin)),
                    COLS_MOV, antes=solo_columnas_mov(df), nota="edición desde administrar historial",
                    normalizador=lambda d: solo_columnas_mov(normalizar_movimientos(d)),)
                if exito:
                    st.session_state.modo_revision = False
                    st.session_state.ignorar_alerta_cuadre = False
                    msg_success(st, mensaje)
                    st.rerun()
                else:
                    msg_error(st, mensaje)
        with b2:
            if st.button("🔄 Recargar desde Google Sheets", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
