#!/usr/bin/env python3
"""
Guardián del Reposo
Programa apagado, reinicio o suspensión con cuenta atrás y aviso previo.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_ESTADO = Path.home() / ".config" / "automatizaciones" / "reposo.json"
logger = configurar_logger("guardian_reposo")

ACCIONES = {
    "shutdown": ["systemctl", "poweroff"],
    "restart": ["systemctl", "reboot"],
    "suspend": ["systemctl", "suspend"],
}

MENU_MAP = {
    "Apagar en 30 minutos": ("shutdown", "30m"),
    "Apagar en 1 hora": ("shutdown", "1h"),
    "Apagar en 2 horas": ("shutdown", "2h"),
    "Reiniciar en 30 minutos": ("restart", "30m"),
    "Reiniciar en 1 hora": ("restart", "1h"),
    "Suspender en 30 minutos": ("suspend", "30m"),
    "Cancelar programado": ("cancel", ""),
}

NOMBRES_ACCION = {
    "shutdown": "Apagado",
    "restart": "Reinicio",
    "suspend": "Suspensión",
}

# Cota máxima de tiempo aceptable para programar una acción.
# Cambiar este valor para ajustar el límite (p. ej. 48 para 48h).
COTA_MAXIMA_HORAS = 24


def parsear_tiempo(texto: str) -> int | None:
    texto = texto.strip().lower()
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", texto)
    if not match or not any(match.groups()):
        return None
    horas = int(match.group(1) or 0)
    minutos = int(match.group(2) or 0)
    return horas * 3600 + minutos * 60


def _formato_tiempo(segundos: int) -> str:
    if segundos < 60:
        return f"{segundos}s"
    m = segundos // 60
    if m < 60:
        return f"{m} minutos"
    h = m // 60
    r = m % 60
    if r == 0:
        return f"{h}h"
    return f"{h}h {r}m"


def guardar_estado(accion: str, hora_objetivo: str, pid: int):
    RUTA_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    RUTA_ESTADO.write_text(json.dumps({
        "accion": accion,
        "hora_objetivo": hora_objetivo,
        "pid": pid,
    }), encoding="utf-8")


def leer_estado() -> dict | None:
    if not RUTA_ESTADO.exists():
        return None
    try:
        data = json.loads(RUTA_ESTADO.read_text(encoding="utf-8"))
        try:
            os.kill(data["pid"], 0)
        except OSError:
            RUTA_ESTADO.unlink()
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        RUTA_ESTADO.unlink()
        return None


def limpiar_estado():
    if RUTA_ESTADO.exists():
        RUTA_ESTADO.unlink()


def programar(accion: str, segundos: int, aviso_previo: int = 300):
    nombre = NOMBRES_ACCION.get(accion, accion)
    hora_obj = datetime.now() + timedelta(seconds=segundos)
    tiempo_fmt = _formato_tiempo(segundos)

    guardar_estado(accion, hora_obj.strftime("%H:%M"), os.getpid())
    logger.info(f"{nombre} programado en {tiempo_fmt} (a las {hora_obj.strftime('%H:%M')})")
    notificar(
        "guardian_reposo",
        f"{nombre} programado en {tiempo_fmt} (a las {hora_obj.strftime('%H:%M')})",
        "info", 5000,
    )

    def _cancelado(sig, frame):
        limpiar_estado()
        logger.info(f"{nombre} cancelado")
        notificar("guardian_reposo", f"{nombre} cancelado", "info", 5000)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cancelado)
    signal.signal(signal.SIGINT, _cancelado)

    espera_aviso = max(0, segundos - aviso_previo)
    if espera_aviso > 0:
        time.sleep(espera_aviso)

    restante = min(segundos, aviso_previo)
    if restante > 0 and segundos > aviso_previo:
        logger.info(f"{nombre} en {_formato_tiempo(restante)}")
        notificar(
            "guardian_reposo",
            f"El sistema se {'apagará' if accion == 'shutdown' else 'reiniciará' if accion == 'restart' else 'suspenderá'} en {_formato_tiempo(restante)}",
            "aviso", 0,
        )
        time.sleep(restante)

    limpiar_estado()
    logger.info(f"Ejecutando {nombre.lower()}...")
    cmd = ACCIONES[accion]
    subprocess.run(cmd)


def cancelar():
    estado = leer_estado()
    if not estado:
        print("No hay nada programado")
        notificar("guardian_reposo", "No hay nada programado", "info", 3000)
        return

    try:
        os.kill(estado["pid"], signal.SIGTERM)
    except OSError:
        pass
    limpiar_estado()


def mostrar_estado():
    estado = leer_estado()
    if not estado:
        print("No hay nada programado")
        return
    nombre = NOMBRES_ACCION.get(estado["accion"], estado["accion"])
    print(f"{nombre} programado para las {estado['hora_objetivo']} (PID: {estado['pid']})")


def abrir_menu():
    config = cargar_config(RUTA_AUTO)
    opciones = config.get("menu", {}).get("opciones", list(MENU_MAP.keys()))

    estado = leer_estado()
    if estado:
        nombre = NOMBRES_ACCION.get(estado["accion"], estado["accion"])
        placeholder = f"{nombre} a las {estado['hora_objetivo']} — elige acción"
    else:
        placeholder = "Programar apagado/reinicio/suspensión"

    entrada = "\n".join(opciones)
    result = subprocess.run(
        ["walker", "--dmenu", "--placeholder", placeholder],
        input=entrada, capture_output=True, text=True,
    )

    if result.returncode != 0 or not result.stdout.strip():
        return

    seleccion = result.stdout.strip()
    if seleccion not in MENU_MAP:
        return

    accion, tiempo = MENU_MAP[seleccion]
    if accion == "cancel":
        cancelar()
    else:
        segundos = parsear_tiempo(tiempo)
        if segundos:
            programar(accion, segundos)


def validar_tiempo_accion(accion: str, tiempo_str: str) -> int:
    """Valida tiempo y cota para una acción de programación.

    Imprime mensaje de error y hace sys.exit(1) si el tiempo es inválido,
    supera la cota máxima o ya hay una acción programada.

    Devuelve los segundos si la validación pasa.
    """
    segundos = parsear_tiempo(tiempo_str)
    if segundos is None or segundos == 0:
        print(f"Tiempo no válido: {tiempo_str} (usa 30m, 1h, 2h30m...)")
        sys.exit(1)
    cota_segundos = COTA_MAXIMA_HORAS * 3600
    if segundos > cota_segundos:
        print(
            f"Tiempo demasiado largo: {tiempo_str}. "
            f"La cota máxima es {COTA_MAXIMA_HORAS}h. "
            f"Para ajustarla, cambia COTA_MAXIMA_HORAS en guardian_reposo.py."
        )
        sys.exit(1)
    estado = leer_estado()
    if estado:
        nombre = NOMBRES_ACCION.get(estado["accion"], estado["accion"])
        print(f"Ya hay un {nombre.lower()} programado para las {estado['hora_objetivo']}. Cancela primero con: rast cancel")
        sys.exit(1)
    return segundos


def main():
    parser = argparse.ArgumentParser(description="Programar apagado/reinicio/suspensión")
    sub = parser.add_subparsers(dest="comando")

    p_shut = sub.add_parser("shutdown", help="Programar apagado")
    p_shut.add_argument("tiempo", help="Ej: 30m, 1h, 2h30m")

    p_rest = sub.add_parser("restart", help="Programar reinicio")
    p_rest.add_argument("tiempo", help="Ej: 30m, 1h, 2h30m")

    p_susp = sub.add_parser("suspend", help="Programar suspensión")
    p_susp.add_argument("tiempo", help="Ej: 30m, 1h, 2h30m")

    # Subcomando de validación pura: valida tiempo y cota sin programar nada.
    # Usado por el wrapper bash para validar en primer plano antes del & disown.
    p_val = sub.add_parser("validar", help="Validar tiempo sin programar (uso interno del wrapper)")
    p_val.add_argument("accion", choices=["shutdown", "restart", "suspend"])
    p_val.add_argument("tiempo", help="Ej: 30m, 1h, 2h30m")

    sub.add_parser("cancel", help="Cancelar lo programado")
    sub.add_parser("status", help="Mostrar estado")
    sub.add_parser("menu", help="Abrir menú visual")

    args = parser.parse_args()

    if args.comando in ("shutdown", "restart", "suspend"):
        segundos = validar_tiempo_accion(args.comando, args.tiempo)
        programar(args.comando, segundos)
    elif args.comando == "validar":
        # Solo valida: imprime error y sale con exit≠0 si algo falla,
        # o sale silenciosamente con exit 0 si todo es correcto.
        validar_tiempo_accion(args.accion, args.tiempo)
    elif args.comando == "cancel":
        cancelar()
    elif args.comando == "status":
        mostrar_estado()
    elif args.comando == "menu":
        abrir_menu()
    else:
        abrir_menu()


if __name__ == "__main__":
    main()
