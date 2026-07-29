#!/usr/bin/env python3
"""
Jefe de Maquinas — Orquestador Central del Castillo
Muestra el estado de todas las automatizaciones, permite lanzarlas,
pararlas, ver logs y gestionar el ecosistema completo.
Parte del ecosistema: orquestacion.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, RUTA_LOGS

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_ECOSISTEMA = Path(__file__).resolve().parent.parent
RUTA_BIN = Path.home() / ".local" / "bin"

logger = configurar_logger("jefe_de_maquinas")

CONSEJERO = "jefe_de_maquinas"

# -- Colores ANSI ---------------------------------------------------------------

C = {
    "verde": "\033[32m",
    "amarillo": "\033[33m",
    "rojo": "\033[31m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}

# Directorios que no son automatizaciones
_EXCLUIDOS = {"comun", "images", "logs", "__pycache__", "el_arquitecto_del_castillo"}


# -- Descubrimiento de automatizaciones ----------------------------------------

def descubrir_automatizaciones() -> list[dict]:
    """
    Escanea el directorio del ecosistema y devuelve info de cada automatizacion.

    Cada entrada contiene:
      - nombre: nombre del directorio
      - ruta: Path al directorio
      - implementada: True si tiene al menos un .py
      - config: dict cargado de config.toml (o {})
      - descripcion: descripcion del config.toml o ''
      - comando: nombre del wrapper CLI en ~/.local/bin (o None)
      - ultimo_log: datetime de la ultima linea del log (o None)
    """
    automatizaciones = []

    if not RUTA_ECOSISTEMA.is_dir():
        return automatizaciones

    for carpeta in sorted(RUTA_ECOSISTEMA.iterdir()):
        if not carpeta.is_dir():
            continue
        nombre = carpeta.name
        if nombre in _EXCLUIDOS or nombre.startswith(".") or nombre.startswith("__"):
            continue

        tiene_py = any(carpeta.glob("*.py"))
        tiene_idea = (carpeta / "idea.txt").exists()

        # Si no tiene ni .py ni idea.txt, no es una automatizacion
        if not tiene_py and not tiene_idea:
            continue

        config = cargar_config(carpeta)
        descripcion = config.get("general", {}).get("descripcion", "")

        # Buscar wrapper CLI
        comando = _buscar_wrapper(nombre)

        # Buscar ultimo log
        ultimo_log = _ultimo_log(nombre)

        automatizaciones.append({
            "nombre": nombre,
            "ruta": carpeta,
            "implementada": tiene_py,
            "config": config,
            "descripcion": descripcion,
            "comando": comando,
            "ultimo_log": ultimo_log,
        })

    return automatizaciones


def _buscar_wrapper(nombre_auto: str) -> str | None:
    """Busca en ~/.local/bin/ un script que apunte al directorio de la automatizacion."""
    if not RUTA_BIN.is_dir():
        return None

    patron = f"automatizaciones/{nombre_auto}/"

    for fichero in RUTA_BIN.iterdir():
        if not fichero.is_file():
            continue
        try:
            contenido = fichero.read_text(encoding="utf-8", errors="replace")
            if patron in contenido:
                return fichero.name
        except OSError:
            continue

    return None


def _ultimo_log(nombre_auto: str) -> datetime | None:
    """Lee la ultima linea del log de una automatizacion y extrae el timestamp."""
    ruta_log = RUTA_LOGS / f"{nombre_auto}.log"
    if not ruta_log.exists():
        return None

    try:
        contenido = ruta_log.read_text(encoding="utf-8", errors="replace")
        lineas = contenido.strip().splitlines()
        if not lineas:
            return None

        # Formato: "2026-05-26 14:01:09,242 | ..."
        ultima = lineas[-1]
        ts_str = ultima.split(",")[0].strip()
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError, OSError):
        return None


def _hace_cuanto(dt: datetime | None) -> str:
    """Devuelve un string legible de cuanto tiempo ha pasado."""
    if dt is None:
        return "sin datos"

    ahora = datetime.now()
    delta = ahora - dt

    if delta.total_seconds() < 0:
        return "futuro"

    minutos = int(delta.total_seconds() / 60)
    horas = int(delta.total_seconds() / 3600)
    dias = delta.days

    if minutos < 1:
        return "ahora mismo"
    if minutos < 60:
        return f"hace {minutos} min"
    if horas < 24:
        return f"hace {horas}h"
    if dias == 1:
        return "hace 1 dia"
    return f"hace {dias} dias"


# -- Deteccion de servicios systemd --------------------------------------------

def detectar_servicios() -> list[dict]:
    """
    Detecta timers y servicios systemd del usuario vinculados al ecosistema.

    Devuelve lista de dicts con:
      - nombre: nombre de la unidad (ej: tejedor_entorno.timer)
      - tipo: 'timer' o 'service'
      - estado: 'running', 'waiting', 'inactive', 'failed', etc.
      - detalle: info extra (PID, proxima ejecucion, etc.)
    """
    servicios = []

    # Obtener nombres de automatizaciones implementadas
    nombres_auto = set()
    if RUTA_ECOSISTEMA.is_dir():
        for carpeta in RUTA_ECOSISTEMA.iterdir():
            if carpeta.is_dir() and carpeta.name not in _EXCLUIDOS:
                if any(carpeta.glob("*.py")):
                    nombres_auto.add(carpeta.name)

    # Timers
    servicios.extend(_detectar_timers(nombres_auto))

    # Services (daemons que no son timers)
    servicios.extend(_detectar_daemons(nombres_auto))

    return servicios


def _detectar_timers(nombres_auto: set[str]) -> list[dict]:
    """Detecta timers del usuario vinculados a automatizaciones."""
    timers = []

    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "list-timers", "--no-pager", "--all"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return timers

    for linea in resultado.stdout.splitlines():
        for nombre in nombres_auto:
            timer_name = f"{nombre}.timer"
            if timer_name in linea:
                detalle = _extraer_detalle_timer(linea)
                timers.append({
                    "nombre": timer_name,
                    "tipo": "timer",
                    "estado": "waiting",
                    "detalle": detalle,
                })

    return timers


def _extraer_detalle_timer(linea: str) -> str:
    """Extrae la proxima ejecucion de una linea de list-timers."""
    # Formato: "Wed 2026-05-27 12:40:33 CEST 37min left Wed ..."
    partes = linea.strip().split()
    if len(partes) >= 2 and partes[0] == "-":
        return "boot"

    # Intentar extraer el campo LEFT
    try:
        # Buscar la posicion de "left" o "ago"
        for i, parte in enumerate(partes):
            if parte in ("left",):
                # El valor esta justo antes
                if i >= 1:
                    return f"prox: {partes[i - 1]}"
        # Si no hay "left", buscar la fecha NEXT
        if len(partes) >= 3:
            return f"prox: {partes[0]} {partes[1]}"
    except (IndexError, ValueError):
        pass

    return ""


def _detectar_daemons(nombres_auto: set[str]) -> list[dict]:
    """Detecta servicios (daemons) del usuario que no son de timer."""
    daemons = []

    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "list-units", "--type=service", "--no-pager", "--all"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return daemons

    # Recoger nombres que ya estan como timer para no duplicar
    timer_nombres = set()
    try:
        res_timers = subprocess.run(
            ["systemctl", "--user", "list-timers", "--no-pager", "--all"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for linea in res_timers.stdout.splitlines():
            for nombre in nombres_auto:
                if f"{nombre}.timer" in linea:
                    timer_nombres.add(nombre)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    for linea in resultado.stdout.splitlines():
        for nombre in nombres_auto:
            service_name = f"{nombre}.service"
            if service_name in linea and nombre not in timer_nombres:
                estado, detalle = _extraer_estado_servicio(linea, nombre)
                daemons.append({
                    "nombre": service_name,
                    "tipo": "service",
                    "estado": estado,
                    "detalle": detalle,
                })

    return daemons


def _extraer_estado_servicio(linea: str, nombre: str) -> tuple[str, str]:
    """Extrae estado y detalle de una linea de list-units."""
    estado = "unknown"
    detalle = ""

    if "running" in linea:
        estado = "running"
        # Intentar obtener PID
        pid = _obtener_pid(nombre)
        if pid:
            detalle = f"PID {pid}"
    elif "failed" in linea:
        estado = "failed"
    elif "inactive" in linea or "dead" in linea:
        estado = "inactive"
    elif "activating" in linea:
        estado = "activating"

    return estado, detalle


def _obtener_pid(nombre: str) -> str | None:
    """Obtiene el PID principal de un servicio."""
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "show", f"{nombre}.service", "--property=MainPID"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for linea in resultado.stdout.splitlines():
            if linea.startswith("MainPID="):
                pid = linea.split("=", 1)[1].strip()
                if pid and pid != "0":
                    return pid
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# -- Errores recientes ---------------------------------------------------------

def errores_recientes_24h() -> list[dict]:
    """Lee el log global de errores y devuelve los de las ultimas 24h."""
    ruta_log = RUTA_LOGS / "errores_global.log"
    if not ruta_log.exists():
        return []

    ahora = datetime.now()
    errores = []

    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            for linea in f:
                partes = linea.strip().split(" | ", 2)
                if len(partes) != 3:
                    continue
                try:
                    ts = datetime.strptime(partes[0], "%Y-%m-%d %H:%M:%S")
                    delta = ahora - ts
                    if delta.total_seconds() <= 86400:
                        errores.append({
                            "timestamp": ts,
                            "automatizacion": partes[1],
                            "mensaje": partes[2],
                        })
                except ValueError:
                    continue
    except OSError:
        pass

    return errores


# -- Dashboard principal -------------------------------------------------------

def dashboard(automatizaciones: list[dict], servicios: list[dict]):
    """Muestra el dashboard completo del ecosistema."""
    r = C["reset"]
    b = C["bold"]
    d = C["dim"]
    cyan = C["cyan"]
    verde = C["verde"]
    amarillo = C["amarillo"]
    rojo = C["rojo"]

    # Cabecera
    print(f"\n  {b}{cyan}{'=' * 55}{r}")
    print(f"  {b}{cyan}EL CASTILLO — Estado del Ecosistema{r}")
    print(f"  {b}{cyan}{'=' * 55}{r}")

    # Servicios activos
    print(f"\n  {b}SERVICIOS ACTIVOS{r}")
    print(f"  {d}{'-' * 55}{r}")

    if not servicios:
        print(f"  {d}No se detectaron servicios systemd{r}")
    else:
        for srv in servicios:
            if srv["estado"] in ("running", "waiting"):
                icono = f"{verde}[OK]{r}"
            elif srv["estado"] == "failed":
                icono = f"{rojo}[!!]{r}"
            else:
                icono = f"{amarillo}[--]{r}"

            nombre_pad = srv["nombre"].ljust(30)
            estado_pad = srv["estado"].ljust(10)
            detalle = f"({srv['detalle']})" if srv["detalle"] else ""

            print(f"  {icono}  {nombre_pad} {estado_pad} {d}{detalle}{r}")

    # Automatizaciones
    implementadas = [a for a in automatizaciones if a["implementada"]]
    pendientes = [a for a in automatizaciones if not a["implementada"]]

    print(f"\n  {b}AUTOMATIZACIONES ({len(implementadas)} implementadas){r}")
    print(f"  {d}{'-' * 55}{r}")

    for auto in implementadas:
        nombre_pad = auto["nombre"].ljust(24)
        cmd_str = f"cmd: {auto['comando']}" if auto["comando"] else "sin cli"
        cmd_pad = cmd_str.ljust(18)
        ultima = _hace_cuanto(auto["ultimo_log"])
        print(f"  {verde}{nombre_pad}{r} {d}{cmd_pad}{r} {d}ultima: {ultima}{r}")

    if pendientes:
        print(f"\n  {b}PENDIENTES ({len(pendientes)} ideas){r}")
        print(f"  {d}{'-' * 55}{r}")
        for auto in pendientes:
            nombre_pad = auto["nombre"].ljust(24)
            desc = auto["descripcion"] or "sin descripcion"
            if len(desc) > 40:
                desc = desc[:37] + "..."
            print(f"  {amarillo}{nombre_pad}{r} {d}{desc}{r}")

    # Errores recientes
    errores = errores_recientes_24h()

    print(f"\n  {b}ERRORES RECIENTES (ultimas 24h){r}")
    print(f"  {d}{'-' * 55}{r}")

    if not errores:
        print(f"  {verde}Sin errores — todo en orden{r}")
    else:
        for err in errores[-10:]:  # Mostrar maximo 10
            ts = err["timestamp"].strftime("%H:%M:%S")
            auto = err["automatizacion"]
            msg = err["mensaje"]
            if len(msg) > 45:
                msg = msg[:42] + "..."
            print(f"  {rojo}{ts}  {auto}: {msg}{r}")
        if len(errores) > 10:
            print(f"  {d}... y {len(errores) - 10} mas. Ejecuta 'errores' para ver todos.{r}")

    print(f"\n  {b}{cyan}{'=' * 55}{r}\n")


# -- Subcomandos ---------------------------------------------------------------

def cmd_servicios():
    """Muestra el estado de los servicios systemd del ecosistema."""
    r = C["reset"]
    b = C["bold"]
    d = C["dim"]
    cyan = C["cyan"]
    verde = C["verde"]
    amarillo = C["amarillo"]
    rojo = C["rojo"]

    servicios = detectar_servicios()

    print(f"\n  {b}{cyan}EL CASTILLO — Servicios systemd{r}")
    print(f"  {d}{'=' * 55}{r}\n")

    if not servicios:
        print(f"  {d}No se detectaron servicios del ecosistema{r}\n")
        return

    timers = [s for s in servicios if s["tipo"] == "timer"]
    daemons = [s for s in servicios if s["tipo"] == "service"]

    if timers:
        print(f"  {b}TIMERS{r}")
        print(f"  {d}{'-' * 55}{r}")
        for srv in timers:
            icono = f"{verde}[OK]{r}" if srv["estado"] == "waiting" else f"{amarillo}[--]{r}"
            nombre_pad = srv["nombre"].ljust(30)
            detalle = srv["detalle"] if srv["detalle"] else ""
            print(f"  {icono}  {nombre_pad} {d}{detalle}{r}")

    if daemons:
        print(f"\n  {b}DAEMONS{r}")
        print(f"  {d}{'-' * 55}{r}")
        for srv in daemons:
            if srv["estado"] == "running":
                icono = f"{verde}[OK]{r}"
            elif srv["estado"] == "failed":
                icono = f"{rojo}[!!]{r}"
            else:
                icono = f"{amarillo}[--]{r}"
            nombre_pad = srv["nombre"].ljust(30)
            detalle = srv["detalle"] if srv["detalle"] else ""
            print(f"  {icono}  {nombre_pad} {srv['estado'].ljust(10)} {d}{detalle}{r}")

    print()


def cmd_logs(nombre_auto: str, lineas: int = 30):
    """Muestra las ultimas lineas del log de una automatizacion."""
    r = C["reset"]
    b = C["bold"]
    d = C["dim"]
    cyan = C["cyan"]

    ruta_log = RUTA_LOGS / f"{nombre_auto}.log"
    if not ruta_log.exists():
        print(f"  No se encontro log para '{nombre_auto}'", file=sys.stderr)
        print(f"  {d}Ruta esperada: {ruta_log}{r}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  {b}{cyan}Log de {nombre_auto}{r} {d}(ultimas {lineas} lineas){r}")
    print(f"  {d}{'-' * 55}{r}\n")

    try:
        contenido = ruta_log.read_text(encoding="utf-8", errors="replace")
        todas = contenido.strip().splitlines()
        for linea in todas[-lineas:]:
            # Colorear niveles
            if "| ERROR |" in linea or "| CRITICAL |" in linea:
                print(f"  {C['rojo']}{linea}{r}")
            elif "| WARNING |" in linea:
                print(f"  {C['amarillo']}{linea}{r}")
            else:
                print(f"  {d}{linea}{r}")
        print()
    except OSError as e:
        print(f"  Error leyendo log: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_ejecutar(nombre_auto: str):
    """Ejecuta una automatizacion manualmente buscando su script principal."""
    r = C["reset"]
    b = C["bold"]
    d = C["dim"]
    cyan = C["cyan"]
    verde = C["verde"]
    rojo = C["rojo"]

    ruta_dir = RUTA_ECOSISTEMA / nombre_auto
    if not ruta_dir.is_dir():
        print(f"  No se encontro la automatizacion '{nombre_auto}'", file=sys.stderr)
        sys.exit(1)

    # Buscar el script principal: <nombre>.py
    script = ruta_dir / f"{nombre_auto}.py"
    if not script.exists():
        # Buscar cualquier .py
        scripts = list(ruta_dir.glob("*.py"))
        if not scripts:
            print(f"  '{nombre_auto}' no tiene scripts Python implementados", file=sys.stderr)
            sys.exit(1)
        script = scripts[0]

    print(f"  {b}{cyan}Ejecutando {nombre_auto}...{r}")
    print(f"  {d}{script}{r}\n")

    logger.info(f"Ejecucion manual: {nombre_auto}")

    try:
        resultado = subprocess.run(
            [sys.executable, str(script)],
            timeout=120,
        )
        if resultado.returncode == 0:
            print(f"\n  {verde}Ejecucion completada correctamente{r}")
            logger.info(f"Ejecucion manual completada: {nombre_auto} (rc=0)")
        else:
            print(f"\n  {rojo}Ejecucion termino con codigo {resultado.returncode}{r}")
            logger.warning(
                f"Ejecucion manual con error: {nombre_auto} (rc={resultado.returncode})"
            )
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"\n  {rojo}Timeout: la ejecucion supero 120 segundos{r}")
        logger.error(f"Timeout en ejecucion manual: {nombre_auto}")
        sys.exit(1)
    except OSError as e:
        print(f"\n  {rojo}Error ejecutando: {e}{r}")
        logger.error(f"Error ejecutando {nombre_auto}: {e}")
        sys.exit(1)


def cmd_parar(nombre_servicio: str):
    """Para un servicio o timer systemd del usuario."""
    r = C["reset"]
    verde = C["verde"]
    rojo = C["rojo"]

    # Asegurar extension
    if not nombre_servicio.endswith((".service", ".timer")):
        # Intentar timer primero, luego service
        for ext in (".timer", ".service"):
            candidato = nombre_servicio + ext
            check = subprocess.run(
                ["systemctl", "--user", "is-active", candidato],
                capture_output=True,
                text=True,
            )
            if check.stdout.strip() in ("active", "waiting"):
                nombre_servicio = candidato
                break
        else:
            nombre_servicio = nombre_servicio + ".service"

    print(f"  Parando {nombre_servicio}...")
    logger.info(f"Parando servicio: {nombre_servicio}")

    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "stop", nombre_servicio],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if resultado.returncode == 0:
            print(f"  {verde}Parado correctamente{r}")
            logger.info(f"Servicio parado: {nombre_servicio}")
        else:
            error = resultado.stderr.strip() or "error desconocido"
            print(f"  {rojo}Error: {error}{r}")
            logger.error(f"Error parando {nombre_servicio}: {error}")
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"  {rojo}Timeout parando el servicio{r}")
        logger.error(f"Timeout parando {nombre_servicio}")
    except FileNotFoundError:
        print(f"  {rojo}systemctl no encontrado{r}")


def cmd_arrancar(nombre_servicio: str):
    """Arranca un servicio o timer systemd del usuario."""
    r = C["reset"]
    verde = C["verde"]
    rojo = C["rojo"]

    # Asegurar extension
    if not nombre_servicio.endswith((".service", ".timer")):
        for ext in (".timer", ".service"):
            candidato = nombre_servicio + ext
            check = subprocess.run(
                ["systemctl", "--user", "is-enabled", candidato],
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                nombre_servicio = candidato
                break
        else:
            nombre_servicio = nombre_servicio + ".service"

    print(f"  Arrancando {nombre_servicio}...")
    logger.info(f"Arrancando servicio: {nombre_servicio}")

    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "start", nombre_servicio],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if resultado.returncode == 0:
            print(f"  {verde}Arrancado correctamente{r}")
            logger.info(f"Servicio arrancado: {nombre_servicio}")
        else:
            error = resultado.stderr.strip() or "error desconocido"
            print(f"  {rojo}Error: {error}{r}")
            logger.error(f"Error arrancando {nombre_servicio}: {error}")
            sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"  {rojo}Timeout arrancando el servicio{r}")
        logger.error(f"Timeout arrancando {nombre_servicio}")
    except FileNotFoundError:
        print(f"  {rojo}systemctl no encontrado{r}")


def cmd_resumen() -> str:
    """Genera un resumen corto del ecosistema para notificacion."""
    automatizaciones = descubrir_automatizaciones()
    servicios = detectar_servicios()
    errores = errores_recientes_24h()

    impl = sum(1 for a in automatizaciones if a["implementada"])
    pend = sum(1 for a in automatizaciones if not a["implementada"])
    n_srv = len(servicios)
    srv_ok = sum(1 for s in servicios if s["estado"] in ("running", "waiting"))
    n_err = len(errores)

    lineas = [
        f"Automatizaciones: {impl} activas, {pend} pendientes",
        f"Servicios: {srv_ok}/{n_srv} operativos",
    ]

    if n_err > 0:
        lineas.append(f"Errores (24h): {n_err}")
    else:
        lineas.append("Sin errores en 24h")

    resumen = "\n".join(lineas)

    config = cargar_config(RUTA_AUTO)
    duracion = config.get("notificacion", {}).get("duracion", 5000)
    notificar(CONSEJERO, resumen, "info", duracion)

    return resumen


# -- Main / CLI ----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Jefe de Maquinas — Orquestador Central del Castillo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  castillo                    Dashboard completo\n"
            "  castillo --servicios        Estado de servicios systemd\n"
            "  castillo --logs limpiador   Ultimas lineas del log\n"
            "  castillo --ejecutar limpiador  Ejecutar manualmente\n"
            "  castillo --parar tejedor_entorno  Parar timer/servicio\n"
            "  castillo --arrancar tejedor_entorno  Arrancar timer/servicio\n"
            "  castillo --resumen          Resumen corto + notificacion\n"
        ),
    )

    parser.add_argument(
        "--servicios",
        action="store_true",
        help="Estado de los servicios systemd (timers + daemons)",
    )
    parser.add_argument(
        "--logs",
        metavar="AUTO",
        help="Muestra ultimas lineas del log de una automatizacion",
    )
    parser.add_argument(
        "--lineas",
        type=int,
        default=30,
        help="Numero de lineas a mostrar con --logs (default: 30)",
    )
    parser.add_argument(
        "--ejecutar",
        metavar="AUTO",
        help="Ejecuta una automatizacion manualmente",
    )
    parser.add_argument(
        "--parar",
        metavar="SERVICIO",
        help="Para un servicio/timer systemd",
    )
    parser.add_argument(
        "--arrancar",
        metavar="SERVICIO",
        help="Arranca un servicio/timer systemd",
    )
    parser.add_argument(
        "--resumen",
        action="store_true",
        help="Resumen corto de todo (envia notificacion)",
    )

    args = parser.parse_args()

    # Despacho
    if args.servicios:
        cmd_servicios()
    elif args.logs is not None:
        if not args.logs.strip():
            print("  Error: --logs requiere un nombre de automatizacion", file=sys.stderr)
            sys.exit(1)
        cmd_logs(args.logs, args.lineas)
    elif args.ejecutar is not None:
        if not args.ejecutar.strip():
            print("  Error: --ejecutar requiere un nombre de automatizacion", file=sys.stderr)
            sys.exit(1)
        cmd_ejecutar(args.ejecutar)
    elif args.parar:
        cmd_parar(args.parar)
    elif args.arrancar:
        cmd_arrancar(args.arrancar)
    elif args.resumen:
        texto = cmd_resumen()
        print(f"  {texto}")
    else:
        # Dashboard completo
        automatizaciones = descubrir_automatizaciones()
        servicios = detectar_servicios()
        dashboard(automatizaciones, servicios)


if __name__ == "__main__":
    main()
