#!/usr/bin/env python3
"""
Vigilante de Temperatura
Monitoriza temperaturas de CPU/GPU/NVMe y alerta por umbrales.
"""

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("vigilante_temperatura")

_ejecutando = True


def _signal_handler(sig, frame):
    global _ejecutando
    logger.info("Señal de parada recibida, cerrando...")
    _ejecutando = False


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def leer_temperaturas(chips_filtro: list[str] | None = None) -> dict[str, float]:
    result = subprocess.run(
        ["sensors", "-j"], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        logger.error(f"Error ejecutando sensors: {result.stderr}")
        return {}

    try:
        datos = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando sensors JSON: {e}")
        return {}

    temperaturas = {}
    for chip, entradas in datos.items():
        if chips_filtro and chip not in chips_filtro:
            continue
        if not isinstance(entradas, dict):
            continue
        for entrada, valores in entradas.items():
            if not isinstance(valores, dict):
                continue
            for clave, valor in valores.items():
                if clave.endswith("_input") and isinstance(valor, (int, float)):
                    nombre = f"{chip}/{entrada}"
                    temperaturas[nombre] = float(valor)

    return temperaturas


def evaluar_temperaturas(
    temperaturas: dict[str, float],
    umbral_aviso: float,
    umbral_critico: float,
    cooldowns: dict[str, float],
    cooldown_seg: int,
) -> dict[str, float]:
    ahora = time.time()
    alertas_enviadas = {}

    for sensor, temp in temperaturas.items():
        if temp >= umbral_critico:
            nivel = "critico"
            mensaje = f"CPU en peligro ({temp:.0f}°C) — cierra programas ahora"
        elif temp >= umbral_aviso:
            nivel = "aviso"
            mensaje = f"CPU caliente ({temp:.0f}°C) — revisa la ventilación"
        else:
            if sensor in cooldowns:
                del cooldowns[sensor]
                notificar(
                    "vigilante_temperatura",
                    f"Temperatura normalizada ({temp:.0f}°C)",
                    "exito",
                    5000,
                )
                logger.info(f"{sensor}: normalizada a {temp:.0f}°C")
            continue

        ultimo = cooldowns.get(sensor, 0)
        if ahora - ultimo >= cooldown_seg:
            severidad = "critico" if nivel == "critico" else "aviso"
            notificar("vigilante_temperatura", mensaje, severidad, 0 if nivel == "critico" else 8000)
            logger.warning(f"{sensor}: {temp:.0f}°C [{nivel}]")
            cooldowns[sensor] = ahora
            alertas_enviadas[sensor] = temp

    return alertas_enviadas


def main():
    parser = argparse.ArgumentParser(
        description="Vigilante de Temperatura — monitoriza CPU/GPU/NVMe y alerta por umbrales.",
        epilog=(
            "Ejemplos:\n"
            "  vigilante_temperatura --once      Una lectura y sale\n"
            "  vigilante_temperatura --status    Igual que --once\n"
            "  vigilante_temperatura --daemon    Modo continuo (por defecto)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--once", "--status", dest="once", action="store_true",
        help="Realiza una sola lectura de temperatura y sale",
    )
    modo.add_argument(
        "--daemon", action="store_true",
        help="Modo continuo — monitoriza hasta recibir SIGTERM/SIGINT (comportamiento por defecto)",
    )
    args = parser.parse_args()

    # Comprobar que sensors está disponible
    if not shutil.which("sensors"):
        logger.error("Comando 'sensors' no encontrado — instala lm_sensors")
        print("Error: 'sensors' no encontrado. Instala lm_sensors.", file=sys.stderr)
        sys.exit(1)

    config = cargar_config(RUTA_AUTO)
    intervalo = config.get("general", {}).get("intervalo_segundos", 60)
    umbral_aviso = config.get("umbrales", {}).get("aviso", 75)
    umbral_critico = config.get("umbrales", {}).get("critico", 90)
    cooldown_seg = config.get("cooldown", {}).get("segundos", 300)
    chips = config.get("sensores", {}).get("chips", [])

    if args.once:
        # Modo --once/--status: una lectura y salida inmediata
        temperaturas = leer_temperaturas(chips or None)
        if not temperaturas:
            print("No se pudieron leer temperaturas.", file=sys.stderr)
            sys.exit(1)
        for sensor, temp in sorted(temperaturas.items()):
            print(f"  {sensor}: {temp:.1f}°C")
        logger.info("Lectura puntual completada: %d sensores", len(temperaturas))
        return

    # Modo daemon (--daemon explícito o sin flags)
    logger.info(
        f"Iniciando vigilante (intervalo={intervalo}s, aviso={umbral_aviso}°C, "
        f"critico={umbral_critico}°C, chips={chips or 'todos'})"
    )

    cooldowns: dict[str, float] = {}
    temp_max_sesion: float = 0

    while _ejecutando:
        temperaturas = leer_temperaturas(chips or None)

        if temperaturas:
            max_temp = max(temperaturas.values())
            if max_temp > temp_max_sesion:
                temp_max_sesion = max_temp
                logger.info(f"Nuevo pico de sesión: {temp_max_sesion:.0f}°C")

            evaluar_temperaturas(
                temperaturas, umbral_aviso, umbral_critico, cooldowns, cooldown_seg
            )

        time.sleep(intervalo)

    logger.info(f"Vigilante detenido. Pico máximo de sesión: {temp_max_sesion:.0f}°C")


if __name__ == "__main__":
    main()
