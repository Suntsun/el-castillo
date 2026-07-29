#!/usr/bin/env python3
"""
Tejedor de Entorno — Rotador de Wallpapers
Elige fondo de pantalla según la hora del día y lo aplica con Omarchy.
Parte del ecosistema: personalización automática.
"""

import argparse
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("tejedor_entorno")

EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

FRANJAS = {
    "manana": (6, 12),
    "dia": (12, 18),
    "tarde": (18, 21),
    "noche": (21, 6),
}


def franja_actual(hora: int | None = None) -> str:
    """Devuelve la franja horaria según la hora actual."""
    if hora is None:
        hora = datetime.now().hour
    if 6 <= hora < 12:
        return "manana"
    if 12 <= hora < 18:
        return "dia"
    if 18 <= hora < 21:
        return "tarde"
    return "noche"


def obtener_wallpapers(carpeta: Path) -> list[Path]:
    """Lista los wallpapers válidos de una carpeta."""
    if not carpeta.exists():
        return []
    return [f for f in carpeta.iterdir() if f.is_file() and f.suffix.lower() in EXTENSIONES]


def aplicar_wallpaper(imagen: Path) -> bool:
    """Aplica un wallpaper actualizando el symlink y reiniciando swaybg."""
    symlink = Path.home() / ".config/omarchy/current/background"
    try:
        imagen_real = imagen.resolve()
        if not imagen_real.is_file():
            logger.error(f"No existe: {imagen_real}")
            return False

        symlink.unlink(missing_ok=True)
        symlink.symlink_to(imagen_real)

        subprocess.run(["pkill", "-x", "swaybg"], capture_output=True)
        import time
        time.sleep(0.3)
        subprocess.Popen(
            ["setsid", "swaybg", "-i", str(symlink), "-m", "fill"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        logger.error(f"Error aplicando wallpaper: {e}")
        return False


def main():
    # Interceptar --help/-h antes de hacer nada con el wallpaper.
    # El comportamiento por defecto (sin flags) DEBE seguir aplicando el wallpaper
    # porque tejedor_entorno.timer llama a ExecStart sin argumentos.
    parser = argparse.ArgumentParser(
        description="Tejedor de Entorno — Rotador de wallpapers según la franja horaria.",
        epilog=(
            "Sin argumentos: aplica un wallpaper aleatorio para la franja horaria actual.\n"
            "El timer systemd tejedor_entorno.timer invoca este script sin argumentos.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True,
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Aplica el wallpaper una vez y sale (equivalente al comportamiento por defecto)",
    )
    # parse_known_args para que --help sea manejado por argparse directamente
    # sin interferir con la ejecución normal cuando no hay flags.
    args, _ = parser.parse_known_args()
    # Si se pasó --help argparse imprime y hace sys.exit(0) automáticamente.
    # Si se pasó --once o nada, continuar con la lógica normal.

    config = cargar_config(RUTA_AUTO)
    cfg_notif = config.get("notificacion", {})
    cfg_general = config.get("general", {})
    silencioso = cfg_general.get("silencioso", False)
    ruta_fondos = Path(cfg_general.get(
        "ruta_fondos",
        str(Path.home() / ".config/omarchy/backgrounds/matte-black"),
    )).expanduser()

    franja = franja_actual()
    carpeta = ruta_fondos / franja
    wallpapers = obtener_wallpapers(carpeta)

    if not wallpapers:
        logger.warning(f"No hay wallpapers en {carpeta}, probando omarchy theme bg next")
        subprocess.run(["omarchy", "theme", "bg", "next"], capture_output=True, timeout=10)
        return

    elegido = random.choice(wallpapers)
    logger.info(f"Franja: {franja} | Elegido: {elegido.name} (de {len(wallpapers)} disponibles)")

    if aplicar_wallpaper(elegido):
        logger.info("Wallpaper aplicado correctamente")
        if not silencioso:
            mensajes = {
                "manana": "Buenos días, nuevo amanecer en tu pantalla",
                "dia": "Paisaje fresco para la jornada",
                "tarde": "Atardecer dorado para ti",
                "noche": "Cielo nocturno para relajarte",
            }
            notificar(
                "tejedor_entorno",
                mensajes.get(franja, "Nuevo fondo de pantalla listo"),
                cfg_notif.get("severidad", "info"),
                cfg_notif.get("duracion", 3000),
            )
    else:
        logger.error("No se pudo aplicar el wallpaper")
        sys.exit(1)


if __name__ == "__main__":
    main()
