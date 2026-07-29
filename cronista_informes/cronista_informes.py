#!/usr/bin/env python3
"""
Cronista de Informes — Generador de Informes Semanal
Genera un resumen completo del ecosistema de automatizaciones:
ejecuciones, errores, estado del sistema, actividad por automatización.
Parte del ecosistema: monitorización.
"""

import argparse
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, RUTA_LOGS

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("cronista_informes")

CONSEJERO = "cronista_informes"

# Regex para parsear líneas del formato estándar de logs
# Formato: "2026-05-26 14:01:09,242 | INFO | actualizador | mensaje"
RE_LINEA_LOG = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \| (\w+) \| (\w+) \| (.+)$"
)

# Regex para parsear líneas del log global de errores
# Formato: "2026-05-26 14:01:09 | actualizador | mensaje"
RE_LINEA_ERROR = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (.+?) \| (.+)$"
)

# Logs que no son de automatizaciones reales
LOGS_EXCLUIDOS = {"cronista_errores", "errores_global", "test_comun", "cronista_informes"}

# Patrones para detectar actividad específica de ciertas automatizaciones
PATRON_WALLPAPER = re.compile(r"Wallpaper (cambiado|aplicado) correctamente", re.IGNORECASE)
PATRON_TRADUCCION = re.compile(r"Traducción completada", re.IGNORECASE)
PATRON_DESCARGA = re.compile(r"Descarga completada", re.IGNORECASE)
PATRON_ZEN_ACTIVADO = re.compile(r"Modo Zen activado", re.IGNORECASE)


# -- Ruta de informes -----------------------------------------------------------

def _ruta_informes(config: dict) -> Path:
    """Obtiene la ruta del directorio de informes desde la config."""
    cfg = config.get("informe", {})
    ruta_str = cfg.get("ruta_informes", "~/Escritorio/automatizaciones/logs/informes")
    return Path(ruta_str).expanduser()


# -- Recolección de datos del sistema ------------------------------------------

def obtener_disco() -> dict:
    """Obtiene el uso de disco del sistema de archivos raíz."""
    try:
        uso = shutil.disk_usage("/")
        porcentaje = round((uso.used / uso.total) * 100)
        libre = round((uso.free / uso.total) * 100)
        return {
            "porcentaje_usado": porcentaje,
            "porcentaje_libre": libre,
            "total_gb": round(uso.total / (1024**3), 1),
            "usado_gb": round(uso.used / (1024**3), 1),
            "libre_gb": round(uso.free / (1024**3), 1),
        }
    except OSError as e:
        logger.error(f"Error obteniendo uso de disco: {e}")
        return {"porcentaje_usado": -1, "porcentaje_libre": -1}


def obtener_temperatura() -> int | None:
    """Lee la temperatura de la CPU desde /sys/class/thermal/."""
    ruta_thermal = Path("/sys/class/thermal")
    if not ruta_thermal.exists():
        return None

    try:
        for zona in sorted(ruta_thermal.glob("thermal_zone*/temp")):
            texto = zona.read_text().strip()
            temp_mili = int(texto)
            # Los valores se reportan en miligrados
            return temp_mili // 1000
    except (OSError, ValueError) as e:
        logger.warning(f"No se pudo leer temperatura: {e}")
        return None


def obtener_uptime() -> str:
    """Lee el uptime del sistema desde /proc/uptime."""
    try:
        texto = Path("/proc/uptime").read_text().strip()
        segundos_total = int(float(texto.split()[0]))
        dias = segundos_total // 86400
        horas = (segundos_total % 86400) // 3600
        minutos = (segundos_total % 3600) // 60

        partes = []
        if dias > 0:
            partes.append(f"{dias} dia{'s' if dias != 1 else ''}")
        if horas > 0:
            partes.append(f"{horas} hora{'s' if horas != 1 else ''}")
        if minutos > 0 and dias == 0:
            partes.append(f"{minutos} minuto{'s' if minutos != 1 else ''}")

        return " ".join(partes) if partes else "menos de 1 minuto"
    except (OSError, ValueError) as e:
        logger.warning(f"No se pudo leer uptime: {e}")
        return "desconocido"


def obtener_timers() -> tuple[int, list[str]]:
    """Obtiene la lista de timers de usuario activos."""
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "list-timers", "--no-pager", "--no-legend"],
            capture_output=True, text=True, timeout=10,
        )
        lineas = [l.strip() for l in resultado.stdout.strip().splitlines() if l.strip()]
        nombres = []
        for linea in lineas:
            # La última columna es el nombre del timer (UNIT)
            partes = linea.split()
            if partes:
                nombres.append(partes[-1])
        return len(nombres), nombres
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"No se pudieron obtener timers: {e}")
        return 0, []


# -- Análisis de logs ----------------------------------------------------------

def _parsear_log(ruta_log: Path, desde: datetime, hasta: datetime) -> list[dict]:
    """Lee un log y devuelve las líneas parseadas dentro del rango de fechas."""
    if not ruta_log.exists():
        return []

    entradas = []
    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            for linea in f:
                match = RE_LINEA_LOG.match(linea.strip())
                if not match:
                    continue
                ts_str, nivel, nombre, mensaje = match.groups()
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if desde <= ts <= hasta:
                    entradas.append({
                        "timestamp": ts,
                        "nivel": nivel,
                        "nombre": nombre,
                        "mensaje": mensaje,
                    })
    except OSError as e:
        logger.warning(f"No se pudo leer {ruta_log.name}: {e}")

    return entradas


def contar_actividad(desde: datetime, hasta: datetime) -> dict[str, int]:
    """Cuenta líneas INFO por automatización en el rango de fechas."""
    conteo: dict[str, int] = {}

    if not RUTA_LOGS.exists():
        return conteo

    for log_file in sorted(RUTA_LOGS.glob("*.log")):
        nombre = log_file.stem
        if nombre in LOGS_EXCLUIDOS:
            continue

        entradas = _parsear_log(log_file, desde, hasta)
        total_info = sum(1 for e in entradas if e["nivel"] == "INFO")
        if total_info > 0:
            conteo[nombre] = total_info

    return conteo


def contar_errores_global(desde: datetime, hasta: datetime) -> dict[str, list[str]]:
    """Lee errores_global.log y agrupa por automatización."""
    ruta_global = RUTA_LOGS / "errores_global.log"
    errores: dict[str, list[str]] = {}

    if not ruta_global.exists():
        return errores

    # Revisar también backups recientes del global
    ficheros = [ruta_global]
    for bak in sorted(RUTA_LOGS.glob("errores_global_*.log.bak")):
        ficheros.append(bak)

    for fichero in ficheros:
        try:
            with open(fichero, encoding="utf-8", errors="replace") as f:
                for linea in f:
                    match = RE_LINEA_ERROR.match(linea.strip())
                    if not match:
                        continue
                    ts_str, auto, mensaje = match.groups()
                    try:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if desde <= ts <= hasta:
                        errores.setdefault(auto, []).append(mensaje)
        except OSError as e:
            logger.warning(f"No se pudo leer {fichero.name}: {e}")

    return errores


def contar_especificos(desde: datetime, hasta: datetime) -> dict[str, int]:
    """Cuenta actividad específica: wallpapers, traducciones, descargas, sesiones zen."""
    especificos: dict[str, int] = {}

    patrones = {
        "wallpapers": ("tejedor_entorno", PATRON_WALLPAPER),
        "traducciones": ("traductor_terminal", PATRON_TRADUCCION),
        "descargas": ("cazador_medios", PATRON_DESCARGA),
        "sesiones_zen": ("guardador_silencio", PATRON_ZEN_ACTIVADO),
    }

    for clave, (nombre_log, patron) in patrones.items():
        ruta_log = RUTA_LOGS / f"{nombre_log}.log"
        if not ruta_log.exists():
            continue

        entradas = _parsear_log(ruta_log, desde, hasta)
        total = sum(1 for e in entradas if patron.search(e["mensaje"]))
        if total > 0:
            especificos[clave] = total

    return especificos


# -- Generación del informe ----------------------------------------------------

def _estado_global(total_errores: int) -> tuple[str, str]:
    """Determina el estado global: VERDE/AMARILLO/ROJO y su descripción."""
    if total_errores == 0:
        return "VERDE", "Todo en orden"
    elif total_errores <= 5:
        return "AMARILLO", f"{total_errores} errores detectados"
    else:
        return "ROJO", f"{total_errores} errores — requiere atencion"


def _severidad_notificacion(total_errores: int) -> str:
    """Determina la severidad de la notificación según errores."""
    if total_errores == 0:
        return "exito"
    elif total_errores <= 5:
        return "aviso"
    else:
        return "error"


def generar_informe(config: dict, fecha_fin: datetime | None = None) -> str:
    """
    Genera el informe semanal completo en texto plano.

    Args:
        config: Configuración cargada desde config.toml.
        fecha_fin: Fecha final del periodo (default: ahora).

    Returns:
        Texto del informe completo.
    """
    if fecha_fin is None:
        fecha_fin = datetime.now()

    fecha_inicio = fecha_fin - timedelta(days=7)

    logger.info(f"Generando informe: {fecha_inicio:%Y-%m-%d} al {fecha_fin:%Y-%m-%d}")

    # Recopilar datos
    actividad = contar_actividad(fecha_inicio, fecha_fin)
    errores = contar_errores_global(fecha_inicio, fecha_fin)
    especificos = contar_especificos(fecha_inicio, fecha_fin)
    disco = obtener_disco()
    temperatura = obtener_temperatura()
    uptime = obtener_uptime()
    num_timers, nombres_timers = obtener_timers()

    # Calcular totales
    total_ejecuciones = sum(actividad.values())
    total_errores = sum(len(msgs) for msgs in errores.values())
    estado, desc_estado = _estado_global(total_errores)

    # Construir informe
    lineas: list[str] = []
    sep = "=" * 55
    sep_sub = "-" * 55

    # Cabecera
    lineas.append(f"  {sep}")
    lineas.append(f"  INFORME SEMANAL — {fecha_inicio:%d %b %Y} al {fecha_fin:%d %b %Y}")
    lineas.append(f"  Estado: {estado} — {desc_estado}")
    lineas.append(f"  {sep}")
    lineas.append("")

    # Actividad
    lineas.append("  ACTIVIDAD")
    lineas.append(f"  {sep_sub}")
    if actividad:
        max_nombre = max(len(n) for n in actividad)
        for nombre, conteo in sorted(actividad.items(), key=lambda x: -x[1]):
            lineas.append(f"  {nombre:<{max_nombre}}  {conteo:>4} ejecuciones")
    else:
        lineas.append("  Sin actividad registrada")
    lineas.append("")

    # Actividad específica
    if especificos:
        etiquetas = {
            "wallpapers": ("tejedor_entorno", "cambios de wallpaper"),
            "traducciones": ("traductor_terminal", "traducciones"),
            "descargas": ("cazador_medios", "descargas"),
            "sesiones_zen": ("guardador_silencio", "sesiones zen"),
        }
        tiene_especifico = False
        for clave, (nombre, etiqueta) in etiquetas.items():
            if clave in especificos:
                if not tiene_especifico:
                    lineas.append("  DETALLE")
                    lineas.append(f"  {sep_sub}")
                    tiene_especifico = True
                lineas.append(f"  {nombre:<25} {especificos[clave]:>4} {etiqueta}")
        if tiene_especifico:
            lineas.append("")

    # Errores
    lineas.append(f"  ERRORES ({total_errores})")
    lineas.append(f"  {sep_sub}")
    if errores:
        for auto, msgs in sorted(errores.items()):
            # Agrupar mensajes repetidos
            conteo_msgs = Counter(msgs)
            for msg, cantidad in conteo_msgs.most_common():
                if cantidad > 1:
                    lineas.append(f"  {auto}: {msg} (x{cantidad})")
                else:
                    lineas.append(f"  {auto}: {msg}")
    else:
        lineas.append("  Sin errores registrados")
    lineas.append("")

    # Sistema
    lineas.append("  SISTEMA")
    lineas.append(f"  {sep_sub}")
    if disco["porcentaje_usado"] >= 0:
        lineas.append(
            f"  Disco: {disco['porcentaje_usado']}% usado "
            f"({disco['porcentaje_libre']}% libre)"
        )
    else:
        lineas.append("  Disco: no disponible")

    if temperatura is not None:
        lineas.append(f"  Temperatura: {temperatura} C")
    else:
        lineas.append("  Temperatura: no disponible")

    lineas.append(f"  Uptime: {uptime}")
    lineas.append(f"  Timers activos: {num_timers}")
    lineas.append("")

    # Pie
    lineas.append(f"  {sep}")

    return "\n".join(lineas)


# -- Guardar informe -----------------------------------------------------------

def guardar_informe(texto: str, config: dict, fecha: datetime | None = None) -> Path:
    """Guarda el informe en un archivo de texto."""
    if fecha is None:
        fecha = datetime.now()

    ruta_dir = _ruta_informes(config)
    ruta_dir.mkdir(parents=True, exist_ok=True)

    nombre = f"informe_{fecha:%Y-%m-%d}.txt"
    ruta_archivo = ruta_dir / nombre

    try:
        ruta_archivo.write_text(texto, encoding="utf-8")
        logger.info(f"Informe guardado en {ruta_archivo}")
    except OSError as e:
        logger.error(f"No se pudo guardar informe: {e}")

    return ruta_archivo


# -- Notificación --------------------------------------------------------------

def enviar_notificacion(actividad: dict, errores: dict, disco: dict, config: dict):
    """Envía la notificación resumen del informe."""
    total_ejecuciones = sum(actividad.values())
    total_errores = sum(len(msgs) for msgs in errores.values())
    severidad = _severidad_notificacion(total_errores)

    pct_disco = disco.get("porcentaje_usado", "?")
    msg = (
        f"Informe semanal: {total_ejecuciones} ejecuciones, "
        f"{total_errores} errores, disco al {pct_disco}%"
    )

    cfg_notif = config.get("notificacion", {})
    duracion = cfg_notif.get("duracion", 10000)

    notificar(CONSEJERO, msg, severidad, duracion)
    logger.info(f"Notificacion enviada: {msg} (severidad={severidad})")


# -- Ejecución principal -------------------------------------------------------

def ejecutar(config: dict):
    """Genera, guarda y notifica el informe semanal."""
    ahora = datetime.now()
    fecha_inicio = ahora - timedelta(days=7)

    texto = generar_informe(config, ahora)
    guardar_informe(texto, config, ahora)

    # Datos para notificación
    actividad = contar_actividad(fecha_inicio, ahora)
    errores = contar_errores_global(fecha_inicio, ahora)
    disco = obtener_disco()

    enviar_notificacion(actividad, errores, disco, config)
    logger.info("Informe semanal generado correctamente")


# -- CLI: comando 'informe' ----------------------------------------------------

_COLORES = {
    "rojo": "\033[31m",
    "amarillo": "\033[33m",
    "verde": "\033[32m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _colorear_estado(texto: str) -> str:
    """Aplica color al estado del informe."""
    c = _COLORES
    if "VERDE" in texto:
        return texto.replace("VERDE", f"{c['verde']}VERDE{c['reset']}")
    elif "AMARILLO" in texto:
        return texto.replace("AMARILLO", f"{c['amarillo']}AMARILLO{c['reset']}")
    elif "ROJO" in texto:
        return texto.replace("ROJO", f"{c['rojo']}ROJO{c['reset']}")
    return texto


def _colorear_informe(texto: str) -> str:
    """Aplica colores ANSI al informe para la terminal."""
    c = _COLORES
    lineas_out = []

    for linea in texto.splitlines():
        stripped = linea.strip()

        # Separadores
        if stripped.startswith("===") or stripped.startswith("---"):
            lineas_out.append(f"{c['dim']}{linea}{c['reset']}")
        # Títulos de sección
        elif stripped in ("ACTIVIDAD", "DETALLE", "SISTEMA") or stripped.startswith("ERRORES"):
            lineas_out.append(f"{c['bold']}{c['cyan']}{linea}{c['reset']}")
        # Cabecera INFORME SEMANAL
        elif "INFORME SEMANAL" in stripped:
            lineas_out.append(f"{c['bold']}{linea}{c['reset']}")
        # Estado
        elif stripped.startswith("Estado:"):
            lineas_out.append(f"  {_colorear_estado(stripped)}")
        # Líneas de error
        elif "error" in stripped.lower() and ":" in stripped and not stripped.startswith("ERRORES"):
            lineas_out.append(f"{c['rojo']}{linea}{c['reset']}")
        # Sin errores
        elif "Sin errores" in stripped:
            lineas_out.append(f"{c['verde']}{linea}{c['reset']}")
        else:
            lineas_out.append(linea)

    return "\n".join(lineas_out)


def cmd_mostrar(config: dict, semana: str | None = None):
    """Muestra el último informe o el de una semana concreta."""
    from datetime import datetime as _dt

    ruta_dir = _ruta_informes(config)

    if semana:
        # Validar formato antes de intentar acceder al archivo
        try:
            _dt.strptime(semana, "%Y-%m-%d")
        except ValueError:
            print(
                f"Formato de fecha inválido: '{semana}'\n"
                f"Usa el formato YYYY-MM-DD, por ejemplo: 2025-05-26",
                file=sys.stderr,
            )
            sys.exit(1)

        # Buscar informe de una semana específica
        ruta_archivo = ruta_dir / f"informe_{semana}.txt"
        if not ruta_archivo.exists():
            print(f"No se encontro informe para la semana del {semana}", file=sys.stderr)
            print(f"Usa 'informe --lista' para ver informes disponibles", file=sys.stderr)
            sys.exit(1)
    else:
        # Buscar el último informe
        if not ruta_dir.exists():
            print("No hay informes generados aun.", file=sys.stderr)
            print("Ejecuta 'informe --generar' para crear uno.", file=sys.stderr)
            sys.exit(1)

        informes = sorted(ruta_dir.glob("informe_*.txt"))
        if not informes:
            print("No hay informes generados aun.", file=sys.stderr)
            print("Ejecuta 'informe --generar' para crear uno.", file=sys.stderr)
            sys.exit(1)

        ruta_archivo = informes[-1]

    try:
        texto = ruta_archivo.read_text(encoding="utf-8")
        print()
        print(_colorear_informe(texto))
        print()
    except OSError as e:
        print(f"Error leyendo informe: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_generar(config: dict):
    """Genera un nuevo informe ahora."""
    c = _COLORES
    print(f"\n{c['dim']}Generando informe semanal...{c['reset']}")

    ejecutar(config)

    ruta_dir = _ruta_informes(config)
    informes = sorted(ruta_dir.glob("informe_*.txt"))
    if informes:
        print(f"{c['verde']}Informe guardado en: {informes[-1]}{c['reset']}\n")


def cmd_lista(config: dict):
    """Lista informes disponibles."""
    c = _COLORES
    ruta_dir = _ruta_informes(config)

    if not ruta_dir.exists():
        print("No hay informes generados aun.", file=sys.stderr)
        sys.exit(1)

    informes = sorted(ruta_dir.glob("informe_*.txt"))
    if not informes:
        print("No hay informes generados aun.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{c['bold']}{c['cyan']}  Informes disponibles{c['reset']}")
    print(f"{c['dim']}  {'=' * 40}{c['reset']}\n")

    for informe in informes:
        nombre = informe.stem.replace("informe_", "")
        tamano = informe.stat().st_size
        tamano_str = f"{tamano / 1024:.1f} KB" if tamano > 1024 else f"{tamano} B"
        print(f"  {c['cyan']}{nombre}{c['reset']}  {c['dim']}({tamano_str}){c['reset']}")

    print(f"\n{c['dim']}  Usa 'informe --semana YYYY-MM-DD' para ver uno{c['reset']}\n")


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cronista de Informes — Informe semanal del ecosistema"
    )

    parser.add_argument(
        "--generar", action="store_true",
        help="Genera un nuevo informe ahora"
    )
    parser.add_argument(
        "--semana", type=str, default=None,
        help="Muestra informe de una semana especifica (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--lista", action="store_true",
        help="Lista informes disponibles"
    )

    args = parser.parse_args()
    config = cargar_config(RUTA_AUTO)

    if args.generar:
        cmd_generar(config)
    elif args.lista:
        cmd_lista(config)
    elif args.semana is not None:
        # Distinguir: flag sin valor (cadena vacía) → error de usuario
        if args.semana == "":
            print(
                "Error: --semana requiere una fecha en formato YYYY-MM-DD",
                file=sys.stderr,
            )
            sys.exit(2)
        cmd_mostrar(config, args.semana)
    else:
        cmd_mostrar(config)


if __name__ == "__main__":
    main()
