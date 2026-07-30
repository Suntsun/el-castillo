#!/usr/bin/env python3
"""
Cronista de Errores — Logger de Errores Global
Monitoriza los logs de todas las automatizaciones en tiempo real.
Cuando detecta ERROR o CRITICAL, lo registra en un log centralizado
y manda notificación inmediata.
Parte del ecosistema: monitorización.
"""

import argparse
import json
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, RUTA_LOGS

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("cronista_errores")

CONSEJERO = "cronista_errores"

# Regex para parsear líneas del formato estándar de logs del ecosistema
# Formato: "2026-05-26 14:01:09,242 | ERROR | actualizador | mensaje aquí"
RE_LINEA_LOG = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \| (\w+) \| (\w+) \| (.+)$"
)


# -- Gestión de posiciones -----------------------------------------------------

def cargar_posiciones(ruta: str | Path) -> dict[str, int]:
    """Carga las posiciones (byte offsets) guardadas de cada log."""
    ruta = Path(ruta)
    if not ruta.exists():
        return {}
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"No se pudieron cargar posiciones: {e}")
        return {}


def guardar_posiciones(ruta: str | Path, posiciones: dict[str, int]):
    """Guarda las posiciones actuales de lectura de cada log."""
    ruta = Path(ruta)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(posiciones, f, indent=2)
    except OSError as e:
        logger.error(f"No se pudieron guardar posiciones: {e}")


def inicializar_posiciones(fichero_posiciones: str | Path) -> dict[str, int]:
    """Al primer arranque, registra las posiciones actuales sin notificar."""
    posiciones = {}
    if not RUTA_LOGS.exists():
        return posiciones

    for log_file in RUTA_LOGS.glob("*.log"):
        # No monitorizarse a sí mismo ni al log global
        if log_file.stem in ("cronista_errores", "errores_global"):
            continue
        try:
            posiciones[str(log_file)] = log_file.stat().st_size
        except OSError:
            posiciones[str(log_file)] = 0

    guardar_posiciones(fichero_posiciones, posiciones)
    logger.info(f"Posiciones iniciales registradas para {len(posiciones)} logs")
    return posiciones


# -- Escaneo de logs -----------------------------------------------------------

def escanear_logs(
    posiciones: dict[str, int],
    severidades: list[str],
) -> tuple[list[dict], dict[str, int]]:
    """
    Lee las líneas nuevas de todos los logs y filtra errores.

    Devuelve una lista de errores encontrados y las posiciones actualizadas.
    Cada error es un dict con: timestamp, severidad, automatizacion, mensaje.
    """
    errores = []
    nuevas_posiciones = dict(posiciones)

    if not RUTA_LOGS.exists():
        return errores, nuevas_posiciones

    for log_file in RUTA_LOGS.glob("*.log"):
        # No monitorizarse a sí mismo ni al log global
        if log_file.stem in ("cronista_errores", "errores_global"):
            continue

        ruta_str = str(log_file)

        try:
            tamano_actual = log_file.stat().st_size
        except OSError:
            continue

        pos_anterior = posiciones.get(ruta_str, 0)

        # Si el archivo se redujo (rotación de log), leer desde el inicio
        if tamano_actual < pos_anterior:
            pos_anterior = 0

        # Si no hay datos nuevos, saltar
        if tamano_actual <= pos_anterior:
            nuevas_posiciones[ruta_str] = tamano_actual
            continue

        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(pos_anterior)
                contenido = f.read()
                nuevas_posiciones[ruta_str] = f.tell()
        except OSError as e:
            logger.warning(f"No se pudo leer {log_file.name}: {e}")
            continue

        for linea in contenido.splitlines():
            match = RE_LINEA_LOG.match(linea)
            if not match:
                continue

            ts_str, nivel, automatizacion, mensaje = match.groups()
            if nivel in severidades:
                errores.append({
                    "timestamp": ts_str,
                    "severidad": nivel,
                    "automatizacion": automatizacion,
                    "mensaje": mensaje,
                })

    return errores, nuevas_posiciones


# -- Log centralizado de errores -----------------------------------------------

def registrar_en_log_global(errores: list[dict], ruta_log_global: str | Path):
    """Añade los errores al log centralizado."""
    ruta = Path(ruta_log_global).expanduser()
    ruta.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(ruta, "a", encoding="utf-8") as f:
            for error in errores:
                linea = (
                    f"{error['timestamp']} | "
                    f"{error['automatizacion']} | "
                    f"{error['mensaje']}\n"
                )
                f.write(linea)
    except OSError as e:
        logger.error(f"No se pudo escribir en log global: {e}")


# -- Notificaciones de errores -------------------------------------------------

def _resumen_corto(mensaje: str, max_len: int = 60) -> str:
    """Acorta un mensaje de error para la notificación."""
    if len(mensaje) <= max_len:
        return mensaje
    return mensaje[:max_len - 3] + "..."


REMEDIOS: dict[str, str] = {
    "no se encuentra un repositorio git": "Ejecuta 'secretos' dentro de un repo git",
    "gpg": "Revisa tu configuracion GPG: gpg --list-keys",
    "wl-copy": "Instala wl-clipboard: pacman -S wl-clipboard",
    "internet": "Comprueba tu conexion: monitor_red --status",
    "pacman": "Revisa pacman: sudo pacman -Syu",
    "db.lck": "Borra el lock: sudo rm /var/lib/pacman/db.lck",
    "timeout": "El servicio tardo demasiado, reintenta mas tarde",
    "permiso": "Revisa permisos del archivo afectado",
    "espacio": "Libera espacio: limpiador --ejecutar",
    "temperatura": "Tu CPU esta caliente, revisa ventilacion",
    "mako": "Reinicia mako: makoctl reload",
}


_errores_recientes: dict[str, float] = {}


def _clave_error(error: dict) -> str:
    return f"{error['automatizacion']}:{error['mensaje'][:80]}"


def _sugerir_remedio(mensaje: str) -> str:
    """Busca un remedio sugerido para el error."""
    msg_lower = mensaje.lower()
    for patron, remedio in REMEDIOS.items():
        if patron in msg_lower:
            return remedio
    return "Ejecuta 'errores' para mas detalle"


def notificar_errores(
    errores: list[dict],
    duracion: int,
    max_por_ciclo: int,
    cooldown: int = 300,
):
    """Envía notificaciones por errores, con deduplicación y sugerencias."""
    ahora = time.time()
    por_notificar: list[dict] = []

    for error in errores:
        clave = _clave_error(error)
        ultimo = _errores_recientes.get(clave, 0)
        if ahora - ultimo >= cooldown:
            por_notificar.append(error)
            _errores_recientes[clave] = ahora

    # Limpiar entradas viejas del cache
    for clave in list(_errores_recientes):
        if ahora - _errores_recientes[clave] > cooldown * 2:
            del _errores_recientes[clave]

    if not por_notificar:
        return

    for error in por_notificar[:max_por_ciclo]:
        resumen = _resumen_corto(error["mensaje"])
        remedio = _sugerir_remedio(error["mensaje"])
        msg = f"{error['automatizacion']}: {resumen}\n{remedio}"
        notificar(CONSEJERO, msg, "error", duracion)

    sobrantes = len(por_notificar) - max_por_ciclo
    if sobrantes > 0:
        notificar(
            CONSEJERO,
            f"... y {sobrantes} error(es) mas. Ejecuta 'errores' para ver todos.",
            "aviso",
            duracion,
        )


# -- Ciclo del daemon ----------------------------------------------------------

_ejecutando = True


def _signal_handler(signum, frame):
    global _ejecutando
    _ejecutando = False
    logger.info("Senal de parada recibida, cerrando...")


def daemon(config: dict):
    """Bucle principal del daemon: escanea logs periódicamente."""
    global _ejecutando

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    cfg_escaneo = config.get("escaneo", {})
    intervalo = cfg_escaneo.get("intervalo", 30)
    severidades = cfg_escaneo.get("severidades", ["ERROR", "CRITICAL"])
    fichero_posiciones = cfg_escaneo.get(
        "fichero_posiciones", "/tmp/cronista_errores_posiciones.json"
    )

    cfg_log = config.get("log_global", {})
    ruta_log_global = cfg_log.get(
        "ruta",
        str(RUTA_LOGS / "errores_global.log"),
    )

    cfg_notif = config.get("notificacion", {})
    duracion = cfg_notif.get("duracion", 8000)
    max_por_ciclo = cfg_notif.get("max_por_ciclo", 3)
    cooldown = cfg_notif.get("cooldown_duplicados", 300)

    # Cargar o inicializar posiciones
    posiciones = cargar_posiciones(fichero_posiciones)
    if not posiciones:
        logger.info("Primera ejecucion: registrando posiciones iniciales sin notificar")
        posiciones = inicializar_posiciones(fichero_posiciones)

    logger.info(
        f"Daemon iniciado (intervalo={intervalo}s, "
        f"severidades={severidades}, "
        f"logs monitorizados={len(posiciones)})"
    )

    while _ejecutando:
        try:
            errores, posiciones = escanear_logs(posiciones, severidades)
            guardar_posiciones(fichero_posiciones, posiciones)

            if errores:
                logger.info(f"Detectados {len(errores)} errores nuevos")
                registrar_en_log_global(errores, ruta_log_global)
                notificar_errores(errores, duracion, max_por_ciclo, cooldown)

                for error in errores:
                    logger.info(
                        f"  {error['automatizacion']}: {error['mensaje']}"
                    )

        except Exception as e:
            logger.error(f"Error en ciclo de escaneo: {e}")

        # Dormir en intervalos cortos para responder rápido a señales
        for _ in range(intervalo):
            if not _ejecutando:
                break
            time.sleep(1)

    logger.info("Daemon detenido")


# -- CLI: comando 'errores' ----------------------------------------------------

_COLORES = {
    "rojo": "\033[31m",
    "amarillo": "\033[33m",
    "verde": "\033[32m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _leer_log_global(config: dict) -> Path:
    """Obtiene la ruta del log global desde la config."""
    cfg_log = config.get("log_global", {})
    return Path(cfg_log.get("ruta", str(RUTA_LOGS / "errores_global.log")))


def _parsear_linea_global(linea: str) -> dict | None:
    """Parsea una línea del log global: '2026-05-26 14:01:09 | actualizador | mensaje'."""
    partes = linea.strip().split(" | ", 2)
    if len(partes) != 3:
        return None
    try:
        ts = datetime.strptime(partes[0], "%Y-%m-%d %H:%M:%S")
        return {
            "timestamp": ts,
            "automatizacion": partes[1],
            "mensaje": partes[2],
        }
    except ValueError:
        return None


def _filtrar_por_periodo(
    ruta_log: Path, desde: datetime | None = None
) -> list[dict]:
    """Lee el log global y filtra entradas desde una fecha dada."""
    if not ruta_log.exists():
        return []

    entradas = []
    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            for linea in f:
                entrada = _parsear_linea_global(linea)
                if entrada is None:
                    continue
                if desde and entrada["timestamp"] < desde:
                    continue
                entradas.append(entrada)
    except OSError as e:
        print(f"Error leyendo log: {e}", file=sys.stderr)

    return entradas


def cmd_mostrar(config: dict, periodo: str):
    """Muestra errores del periodo indicado con colores ANSI."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    rojo = _COLORES["rojo"]
    cyan = _COLORES["cyan"]

    ruta_log = _leer_log_global(config)

    ahora = datetime.now()
    if periodo == "24h":
        desde = ahora - timedelta(hours=24)
        titulo_periodo = "ultimas 24 horas"
    elif periodo == "semana":
        desde = ahora - timedelta(days=7)
        titulo_periodo = "ultimos 7 dias"
    elif periodo == "todo":
        desde = None
        titulo_periodo = "todo el historial"
    else:
        desde = ahora - timedelta(hours=24)
        titulo_periodo = "ultimas 24 horas"

    entradas = _filtrar_por_periodo(ruta_log, desde)

    print(f"\n{b}{rojo}  Cronista de Errores — {titulo_periodo}{r}")
    print(f"{d}{'=' * 55}{r}\n")

    if not entradas:
        print(f"  {_COLORES['verde']}Sin errores registrados en este periodo{r}\n")
        return

    # Agrupar por automatización
    por_auto: dict[str, list[dict]] = {}
    for e in entradas:
        por_auto.setdefault(e["automatizacion"], []).append(e)

    for auto, errs in sorted(por_auto.items()):
        print(f"  {b}{cyan}{auto}{r} ({len(errs)} error{'es' if len(errs) != 1 else ''}):")
        for e in errs:
            ts = e["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {d}{ts}{r}  {rojo}{e['mensaje']}{r}")
        print()

    total = len(entradas)
    autos = len(por_auto)
    print(f"{d}  Total: {total} error{'es' if total != 1 else ''} en {autos} automatizacion{'es' if autos != 1 else ''}{r}\n")


def cmd_seguir(config: dict):
    """Modo tail -f: sigue el log global en tiempo real."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    rojo = _COLORES["rojo"]
    cyan = _COLORES["cyan"]

    ruta_log = _leer_log_global(config)

    print(f"\n{b}{rojo}  Cronista de Errores — modo seguimiento{r}")
    print(f"{d}  (Ctrl+C para salir){r}\n")

    if not ruta_log.exists():
        ruta_log.parent.mkdir(parents=True, exist_ok=True)
        ruta_log.touch()

    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            # Ir al final del archivo
            f.seek(0, 2)
            while True:
                linea = f.readline()
                if linea:
                    entrada = _parsear_linea_global(linea)
                    if entrada:
                        ts = entrada["timestamp"].strftime("%H:%M:%S")
                        print(
                            f"  {d}{ts}{r}  "
                            f"{cyan}{entrada['automatizacion']}{r}  "
                            f"{rojo}{entrada['mensaje']}{r}"
                        )
                else:
                    time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{d}  Seguimiento terminado{r}\n")


def cmd_limpiar(config: dict):
    """Archiva el log actual y empieza uno limpio."""
    ruta_log = _leer_log_global(config)

    if not ruta_log.exists():
        print("No hay log de errores que limpiar.")
        return

    ahora = datetime.now().strftime("%Y%m%d_%H%M%S")
    archivo = ruta_log.with_name(f"errores_global_{ahora}.log.bak")

    try:
        ruta_log.rename(archivo)
        ruta_log.touch()
        print(f"Log archivado en: {archivo.name}")
        print("Log global limpio creado.")
        logger.info(f"Log global archivado como {archivo.name}")
    except OSError as e:
        print(f"Error al archivar: {e}", file=sys.stderr)
        logger.error(f"Error archivando log global: {e}")


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Cronista de Errores — Logger de Errores Global"
    )
    subparsers = parser.add_subparsers(dest="comando")

    # Subcomando daemon
    sub_daemon = subparsers.add_parser(
        "daemon", help="Ejecutar como daemon de monitorización continua"
    )

    # Subcomando mostrar (default si no se da subcomando)
    sub_mostrar = subparsers.add_parser(
        "mostrar", help="Mostrar errores recientes"
    )
    grupo_periodo = sub_mostrar.add_mutually_exclusive_group()
    grupo_periodo.add_argument(
        "--semana", action="store_true", help="Errores de los ultimos 7 dias"
    )
    grupo_periodo.add_argument(
        "--todo", action="store_true", help="Todo el historial"
    )

    # Subcomando seguir
    sub_seguir = subparsers.add_parser(
        "seguir", help="Seguir el log en tiempo real (tail -f)"
    )

    # Subcomando limpiar
    sub_limpiar = subparsers.add_parser(
        "limpiar", help="Archivar log actual y empezar uno limpio"
    )

    # Argumentos directos para el wrapper CLI (sin subcomando)
    parser.add_argument(
        "--semana", action="store_true",
        help="Errores de los ultimos 7 dias"
    )
    parser.add_argument(
        "--todo", action="store_true",
        help="Todo el historial"
    )
    parser.add_argument(
        "--limpiar", action="store_true",
        help="Archivar log actual y empezar uno limpio"
    )
    parser.add_argument(
        "--seguir", action="store_true",
        help="Seguir el log en tiempo real"
    )

    args = parser.parse_args()
    config = cargar_config(RUTA_AUTO)

    # Si se usan flags directos (sin subcomando, para el wrapper CLI)
    if args.comando is None:
        if args.limpiar:
            cmd_limpiar(config)
        elif args.seguir:
            cmd_seguir(config)
        elif args.semana:
            cmd_mostrar(config, "semana")
        elif args.todo:
            cmd_mostrar(config, "todo")
        else:
            cmd_mostrar(config, "24h")
        return

    # Subcomandos explícitos
    if args.comando == "daemon":
        daemon(config)
    elif args.comando == "mostrar":
        if args.semana:
            cmd_mostrar(config, "semana")
        elif args.todo:
            cmd_mostrar(config, "todo")
        else:
            cmd_mostrar(config, "24h")
    elif args.comando == "seguir":
        cmd_seguir(config)
    elif args.comando == "limpiar":
        cmd_limpiar(config)


if __name__ == "__main__":
    main()
