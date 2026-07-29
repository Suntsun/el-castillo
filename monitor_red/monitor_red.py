#!/usr/bin/env python3
"""
Monitor de Red
Vigila la conexión a internet y registra historial de cortes.
"""

import argparse
import csv
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_HISTORIAL = RUTA_AUTO / "historial_cortes.csv"
logger = configurar_logger("monitor_red")

_ejecutando = True


def _signal_handler(sig, frame):
    global _ejecutando
    logger.info("Señal de parada recibida, cerrando...")
    _ejecutando = False


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def ping(host: str, timeout: int = 5) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout), host],
        capture_output=True,
    )
    return result.returncode == 0


def hay_conexion(hosts: list[str], timeout: int = 5) -> bool:
    for host in hosts:
        if ping(host, timeout):
            return True
    return False


def _formato_duracion(segundos: float) -> str:
    d = timedelta(seconds=int(segundos))
    if d.total_seconds() < 60:
        return f"{int(d.total_seconds())}s"
    minutos = int(d.total_seconds()) // 60
    segs = int(d.total_seconds()) % 60
    if minutos < 60:
        return f"{minutos}m {segs}s"
    horas = minutos // 60
    mins = minutos % 60
    return f"{horas}h {mins}m"


def registrar_corte(inicio: datetime, fin: datetime, duracion: float):
    es_nuevo = not RUTA_HISTORIAL.exists()
    with open(RUTA_HISTORIAL, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if es_nuevo:
            writer.writerow(["inicio", "fin", "duracion_segundos"])
        writer.writerow([
            inicio.strftime("%Y-%m-%d %H:%M:%S"),
            fin.strftime("%Y-%m-%d %H:%M:%S"),
            f"{duracion:.0f}",
        ])


def mostrar_estado():
    config = cargar_config(RUTA_AUTO)
    hosts = config.get("ping", {}).get("hosts", ["1.1.1.1", "8.8.8.8"])
    timeout = config.get("ping", {}).get("timeout", 5)

    conectado = hay_conexion(hosts, timeout)
    print(f"Estado actual: {'CONECTADO' if conectado else 'SIN CONEXIÓN'}")
    print(f"Hosts: {', '.join(hosts)}")

    if not RUTA_HISTORIAL.exists():
        print("\nSin historial de cortes registrado.")
        return

    with open(RUTA_HISTORIAL, encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if not reader:
        print("\nSin cortes registrados.")
        return

    ultimos = reader[-10:]
    print(f"\nÚltimos {len(ultimos)} cortes (de {len(reader)} totales):")
    print(f"{'Inicio':<20} {'Fin':<20} {'Duración':>10}")
    print("-" * 52)
    for fila in ultimos:
        dur = float(fila["duracion_segundos"])
        print(f"{fila['inicio']:<20} {fila['fin']:<20} {_formato_duracion(dur):>10}")


def main():
    parser = argparse.ArgumentParser(description="Monitor de conexión a internet")
    parser.add_argument(
        "--status", action="store_true",
        help="Mostrar estado actual y últimos cortes",
    )
    args = parser.parse_args()

    if args.status:
        mostrar_estado()
        return

    config = cargar_config(RUTA_AUTO)
    general = config.get("general", {})
    ping_config = config.get("ping", {})
    intervalo_normal = general.get("intervalo_normal", 30)
    intervalo_caida = general.get("intervalo_caida", 10)
    hosts = ping_config.get("hosts", ["1.1.1.1", "8.8.8.8"])
    timeout = ping_config.get("timeout", 5)

    logger.info(
        f"Iniciando monitor (intervalo={intervalo_normal}s, "
        f"hosts={hosts}, timeout={timeout}s)"
    )

    en_caida = False
    inicio_caida: datetime | None = None

    while _ejecutando:
        conectado = hay_conexion(hosts, timeout)

        if conectado and en_caida:
            fin = datetime.now()
            duracion = (fin - inicio_caida).total_seconds()
            dur_fmt = _formato_duracion(duracion)
            registrar_corte(inicio_caida, fin, duracion)
            logger.info(f"Conexión restaurada (caída de {dur_fmt})")
            notificar(
                "monitor_red",
                f"Conexión restaurada (caída de {dur_fmt})",
                "exito", 8000,
            )
            en_caida = False
            inicio_caida = None

        elif not conectado and not en_caida:
            inicio_caida = datetime.now()
            en_caida = True
            logger.warning("Conexión a internet perdida")
            notificar(
                "monitor_red",
                "Conexión a internet perdida",
                "error", 0,
            )

        intervalo = intervalo_caida if en_caida else intervalo_normal
        time.sleep(intervalo)

    if en_caida and inicio_caida:
        fin = datetime.now()
        duracion = (fin - inicio_caida).total_seconds()
        registrar_corte(inicio_caida, fin, duracion)
        logger.info(f"Monitor detenido durante caída (duración parcial: {_formato_duracion(duracion)})")

    logger.info("Monitor de red detenido")


if __name__ == "__main__":
    main()
