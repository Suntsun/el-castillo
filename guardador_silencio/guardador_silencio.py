#!/usr/bin/env python3
"""
Guardador del Silencio — Modo Zen
Activa un modo de concentración total: silencia notificaciones,
pausa el rotador de wallpapers y lanza un temporizador.
Parte del ecosistema: herramientas bajo demanda.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, silenciar_notificaciones, activar_notificaciones
from comun import configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
FICHERO_ESTADO = Path("/tmp/zen_activo.json")
FICHERO_PID = Path("/tmp/zen_timer.pid")

logger = configurar_logger("guardador_silencio")

# ── Colores ANSI ─────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"


def _color(texto: str, color: str) -> str:
    """Envuelve texto con código de color ANSI."""
    return f"{color}{texto}{RESET}"


# ── Estado ───────────────────────────────────────────────────────


def leer_estado() -> dict | None:
    """Lee el estado del modo zen desde el fichero temporal."""
    if not FICHERO_ESTADO.exists():
        return None
    try:
        datos = json.loads(FICHERO_ESTADO.read_text())
        return datos
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"No se pudo leer el estado zen: {e}")
        return None


def guardar_estado(duracion_min: int, fin: datetime):
    """Guarda el estado del modo zen en fichero temporal."""
    datos = {
        "inicio": datetime.now().isoformat(),
        "fin": fin.isoformat(),
        "duracion_min": duracion_min,
        "pid": os.getpid(),
    }
    try:
        FICHERO_ESTADO.write_text(json.dumps(datos))
    except OSError as e:
        logger.error(f"No se pudo guardar estado zen: {e}")


def borrar_estado():
    """Borra los ficheros de estado del modo zen."""
    for fichero in (FICHERO_ESTADO, FICHERO_PID):
        if fichero.exists():
            try:
                fichero.unlink()
            except OSError:
                pass


# ── Acciones del sistema ─────────────────────────────────────────


def silenciar_mako():
    """Pone mako en modo do-not-disturb."""
    try:
        subprocess.run(
            ["makoctl", "set-mode", "do-not-disturb"],
            capture_output=True, timeout=5,
        )
        logger.info("Mako en modo do-not-disturb")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"No se pudo silenciar mako: {e}")


def restaurar_mako():
    """Restaura mako al modo normal."""
    try:
        subprocess.run(
            ["makoctl", "set-mode", "default"],
            capture_output=True, timeout=5,
        )
        logger.info("Mako restaurado a modo default")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"No se pudo restaurar mako: {e}")


def pausar_tejedor():
    """Pausa el timer del tejedor_entorno (rotador de wallpapers)."""
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "stop", "tejedor_entorno.timer"],
            capture_output=True, text=True, timeout=10,
        )
        if resultado.returncode == 0:
            logger.info("tejedor_entorno.timer pausado")
        else:
            logger.warning(f"No se pudo pausar tejedor_entorno: {resultado.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"No se pudo pausar tejedor_entorno: {e}")


def reactivar_tejedor():
    """Reactiva el timer del tejedor_entorno."""
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "start", "tejedor_entorno.timer"],
            capture_output=True, text=True, timeout=10,
        )
        if resultado.returncode == 0:
            logger.info("tejedor_entorno.timer reactivado")
        else:
            logger.warning(f"No se pudo reactivar tejedor_entorno: {resultado.stderr.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"No se pudo reactivar tejedor_entorno: {e}")


def abrir_musica_zen(url: str):
    """Abre el stream de música zen en el navegador."""
    if not url:
        return
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"Música zen abierta: {url}")
    except FileNotFoundError:
        logger.warning("No se pudo abrir la música zen")


# ── Activación / Desactivación ───────────────────────────────────


def matar_timer_anterior():
    """Mata el proceso timer anterior si existe."""
    if FICHERO_PID.exists():
        try:
            pid = int(FICHERO_PID.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Timer anterior (PID {pid}) terminado")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        finally:
            try:
                FICHERO_PID.unlink()
            except OSError:
                pass


def activar_zen(duracion_min: int, config_notif: dict, config_zen: dict = None):
    """Activa el modo zen completo."""
    config_zen = config_zen or {}
    estado = leer_estado()
    if estado:
        fin = datetime.fromisoformat(estado["fin"])
        ahora = datetime.now()
        if fin > ahora:
            restante = fin - ahora
            minutos = int(restante.total_seconds() // 60)
            segundos = int(restante.total_seconds() % 60)
            print()
            print(f"  {_color('Modo Zen ya activo', YELLOW + BOLD)}")
            print(f"  Quedan {_color(f'{minutos}m {segundos}s', CYAN)}")
            print(f"  Usa {_color('zen off', GREEN)} para desactivar")
            print()
            return

    fin = datetime.now() + timedelta(minutes=duracion_min)

    # Notificar ANTES de silenciar
    notificar(
        "guardador_silencio",
        f"Modo Zen activado — {duracion_min} minutos. A concentrarse!",
        config_notif.get("severidad_activar", "info"),
        config_notif.get("duracion_activar", 5000),
    )

    # Pequeña pausa para que la notificación se muestre
    time.sleep(1)

    # Silenciar todo
    silenciar_mako()
    silenciar_notificaciones()
    pausar_tejedor()

    # Abrir música zen
    url_musica = config_zen.get("musica_url", "")
    abrir_musica_zen(url_musica)

    # Guardar estado
    guardar_estado(duracion_min, fin)

    # Lanzar timer en background
    _lanzar_timer_background(duracion_min)

    # Feedback en terminal
    hora_fin = fin.strftime("%H:%M")
    print()
    print(f"  {_color('Modo Zen activado', GREEN + BOLD)}")
    print(f"  Duracion:  {_color(f'{duracion_min} minutos', CYAN)}")
    print(f"  Termina a: {_color(hora_fin, CYAN)}")
    print(f"  {_color('Desactivar manualmente:', DIM)} {_color('zen off', GREEN)}")
    print()

    logger.info(f"Modo Zen activado por {duracion_min} minutos (hasta {hora_fin})")


def _lanzar_timer_background(duracion_min: int):
    """Lanza un proceso en background que desactiva zen al terminar el tiempo."""
    script = Path(__file__).resolve()
    pid = subprocess.Popen(
        [sys.executable, str(script), "_timer", str(duracion_min)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).pid

    try:
        FICHERO_PID.write_text(str(pid))
    except OSError as e:
        logger.warning(f"No se pudo guardar PID del timer: {e}")

    logger.info(f"Timer lanzado en background (PID {pid}, {duracion_min}min)")


def ejecutar_timer(duracion_min: int):
    """Proceso timer: espera la duración y luego desactiva zen."""
    segundos = duracion_min * 60
    try:
        time.sleep(segundos)
    except KeyboardInterrupt:
        return

    # Al terminar, desactivar zen
    desactivar_zen_interno()


def desactivar_zen_interno():
    """Desactiva zen: restaura sistema y notifica."""
    # Restaurar todo ANTES de notificar
    restaurar_mako()
    activar_notificaciones()
    reactivar_tejedor()
    borrar_estado()

    # Pequeña pausa para que mako procese el cambio de modo
    time.sleep(0.5)

    # Cargar config para la notificación
    config = cargar_config(RUTA_AUTO)
    config_notif = config.get("notificacion", {})

    notificar(
        "guardador_silencio",
        "Modo Zen terminado. Buen trabajo!",
        config_notif.get("severidad_desactivar", "exito"),
        config_notif.get("duracion_desactivar", 6000),
    )

    logger.info("Modo Zen desactivado")


def desactivar_zen():
    """Desactiva el modo zen manualmente."""
    estado = leer_estado()
    if not estado:
        print()
        print(f"  {_color('El modo Zen no esta activo', YELLOW)}")
        print()
        return

    matar_timer_anterior()
    desactivar_zen_interno()

    print()
    print(f"  {_color('Modo Zen desactivado', GREEN + BOLD)}")
    print(f"  Notificaciones restauradas")
    print(f"  Rotador de wallpapers reactivado")
    print()


def mostrar_estado():
    """Muestra el estado actual del modo zen."""
    estado = leer_estado()
    if not estado:
        print()
        print(f"  {_color('Modo Zen:', DIM)} {_color('inactivo', YELLOW)}")
        print()
        return

    fin = datetime.fromisoformat(estado["fin"])
    ahora = datetime.now()

    if fin <= ahora:
        print()
        print(f"  {_color('Modo Zen:', DIM)} {_color('finalizando...', YELLOW)}")
        print()
        return

    restante = fin - ahora
    minutos = int(restante.total_seconds() // 60)
    segundos = int(restante.total_seconds() % 60)
    hora_fin = fin.strftime("%H:%M")
    duracion_orig = estado.get("duracion_min", "?")

    print()
    print(f"  {_color('Modo Zen:', DIM)} {_color('activo', GREEN + BOLD)}")
    print(f"  Duracion:  {_color(f'{duracion_orig} minutos', CYAN)}")
    print(f"  Restante:  {_color(f'{minutos}m {segundos}s', CYAN + BOLD)}")
    print(f"  Termina a: {_color(hora_fin, CYAN)}")
    print()


# ── CLI ──────────────────────────────────────────────────────────


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="zen",
        description="Modo Zen: concentracion total sin distracciones.",
        epilog=(
            "Ejemplos:\n"
            "  zen              Activa 25 minutos (por defecto)\n"
            "  zen on           Activa 25 minutos\n"
            "  zen 45           Activa 45 minutos\n"
            "  zen on 60        Activa 60 minutos\n"
            "  zen off          Desactiva manualmente\n"
            "  zen status       Muestra estado actual\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "accion",
        nargs="?",
        default="on",
        help="Accion: on, off, status, o numero de minutos",
    )
    parser.add_argument(
        "duracion",
        nargs="?",
        type=int,
        default=None,
        help="Duracion en minutos (por defecto: 25)",
    )
    return parser


def main():
    parser = construir_parser()
    args = parser.parse_args()

    # Comando interno del timer (no se expone al usuario)
    if args.accion == "_timer":
        if args.duracion is None:
            sys.exit(1)
        ejecutar_timer(args.duracion)
        return

    # Cargar configuración
    config = cargar_config(RUTA_AUTO)
    config_zen = config.get("zen", {})
    config_notif = config.get("notificacion", {})
    duracion_defecto = config_zen.get("duracion_minutos", 25)

    # Parsear la acción
    accion = args.accion.lower()

    # Si la acción es un número, interpretar como duración
    try:
        minutos = int(accion)
        if minutos < 1:
            print(
                f"Error: La duración debe ser un entero positivo de minutos (recibido: {minutos}).",
                file=sys.stderr,
            )
            sys.exit(1)
        activar_zen(minutos, config_notif, config_zen)
        return
    except ValueError:
        pass

    match accion:
        case "on":
            duracion = args.duracion if args.duracion is not None else duracion_defecto
            if duracion < 1:
                print(
                    f"Error: La duración debe ser un entero positivo de minutos (recibido: {duracion}).",
                    file=sys.stderr,
                )
                sys.exit(1)
            activar_zen(duracion, config_notif, config_zen)
        case "off":
            desactivar_zen()
        case "status":
            mostrar_estado()
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
