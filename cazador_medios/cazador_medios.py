#!/usr/bin/env python3
"""
Cazador de Medios
Descarga videos y audio de YouTube y SoundCloud.
Parte del ecosistema: herramientas bajo demanda.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("cazador_medios")

# -- Colores ANSI -----------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"


def _color(texto: str, color: str) -> str:
    """Envuelve texto con codigo de color ANSI."""
    return f"{color}{texto}{RESET}"


# -- Deteccion de plataforma ------------------------------------------


def detectar_plataforma(url: str) -> str:
    """
    Detecta si la URL es de YouTube, SoundCloud u otra plataforma.

    Returns:
        'youtube', 'soundcloud' o 'otro'.
    """
    url_lower = url.lower()
    if re.search(r"(youtube\.com|youtu\.be)", url_lower):
        return "youtube"
    if "soundcloud.com" in url_lower:
        return "soundcloud"
    return "otro"


# -- Verificacion de dependencias --------------------------------------


def verificar_dependencias() -> list[str]:
    """Comprueba que yt-dlp y ffmpeg estan instalados.

    Returns:
        Lista de dependencias faltantes (vacia si todo OK).
    """
    faltantes = []
    if not shutil.which("yt-dlp"):
        faltantes.append("yt-dlp")
    if not shutil.which("ffmpeg"):
        faltantes.append("ffmpeg")
    return faltantes


# -- Resolucion de rutas -----------------------------------------------


def resolver_ruta_destino(plataforma: str, config_rutas: dict) -> Path:
    """Determina la carpeta de destino segun plataforma.

    Args:
        plataforma: 'youtube', 'soundcloud' u 'otro'.
        config_rutas: Seccion [rutas] del config.toml.

    Returns:
        Path absoluto de la carpeta destino.
    """
    if plataforma == "soundcloud":
        ruta = config_rutas.get("soundcloud", "~/Música/SoundCloud")
    else:
        ruta = config_rutas.get("youtube", "~/Música/YouTube")

    return Path(ruta).expanduser()


# -- Configuracion de calidad ------------------------------------------

def obtener_formato() -> str:
    """Devuelve el string de formato yt-dlp para mejor audio."""
    return "bestaudio/best"


# -- Descarga -----------------------------------------------------------


def construir_comando(url: str, destino: Path, formato: str,
                      es_playlist: bool) -> list[str]:
    """Construye el comando yt-dlp completo.

    Args:
        url: URL a descargar.
        destino: Carpeta de destino.
        formato: String de formato yt-dlp.
        es_playlist: True si se descarga playlist completa.

    Returns:
        Lista de argumentos para subprocess.
    """
    plantilla_salida = str(destino / "%(title)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", formato,
        "-o", plantilla_salida,
        "--no-mtime",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
    ]

    if not es_playlist:
        cmd.append("--no-playlist")

    cmd.append(url)
    return cmd


def descargar(url: str, destino: Path, formato: str,
              es_playlist: bool) -> bool:
    """Ejecuta la descarga con yt-dlp.

    Muestra el progreso nativo de yt-dlp en la terminal.

    Args:
        url: URL a descargar.
        destino: Carpeta de destino.
        formato: String de formato yt-dlp.
        es_playlist: True si se descarga playlist completa.

    Returns:
        True si la descarga fue exitosa, False en caso contrario.
    """
    destino.mkdir(parents=True, exist_ok=True)

    cmd = construir_comando(url, destino, formato, es_playlist)
    logger.info(f"Ejecutando: {' '.join(cmd)}")

    try:
        resultado = subprocess.run(cmd)
        if resultado.returncode != 0:
            logger.error(f"yt-dlp fallo con codigo {resultado.returncode}")
            return False
        return True
    except FileNotFoundError:
        logger.error("yt-dlp no encontrado en PATH")
        return False
    except KeyboardInterrupt:
        logger.info("Descarga cancelada por el usuario")
        print(f"\n  {_color('Descarga cancelada.', YELLOW)}")
        return False


# -- Interfaz de terminal -----------------------------------------------


def mostrar_info_descarga(url: str, plataforma: str, destino: Path,
                          es_playlist: bool):
    """Muestra un resumen antes de iniciar la descarga."""
    modo = "Playlist" if es_playlist else "Individual"
    plat_color = CYAN if plataforma == "youtube" else MAGENTA

    print()
    print(f"  {_color('Cazador de Medios', BOLD + CYAN)}")
    print(f"  {_color('─' * 40, DIM)}")
    print(f"  {_color('URL:', DIM)}         {url}")
    print(f"  {_color('Plataforma:', DIM)}  {_color(plataforma.title(), plat_color)}")
    print(f"  {_color('Formato:', DIM)}     {_color('MP3 (mejor calidad)', GREEN)}")
    print(f"  {_color('Modo:', DIM)}        {modo}")
    print(f"  {_color('Destino:', DIM)}     {destino}")
    print(f"  {_color('─' * 40, DIM)}")
    print()


def mostrar_error(mensaje: str):
    """Muestra un mensaje de error con formato."""
    print(f"\n  {_color('Error:', RED + BOLD)} {mensaje}\n", file=sys.stderr)


# -- CLI ----------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="yt",
        description="Descarga audio MP3 de YouTube y SoundCloud.",
        epilog=(
            "Ejemplos:\n"
            "  yt https://youtube.com/watch?v=...          Descarga MP3\n"
            "  yt https://youtube.com/watch?v=... -p        Descarga playlist completa\n"
            "  yt https://soundcloud.com/artist/track       Descarga MP3\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="URL del video o audio a descargar",
    )
    parser.add_argument(
        "--playlist", "-p",
        action="store_true",
        help="Descargar playlist completa",
    )
    parser.add_argument(
        "--silent", "-s",
        action="store_true",
        help="Sin notificacion de escritorio",
    )
    return parser


def main():
    parser = construir_parser()
    args = parser.parse_args()

    # -- Sin URL: mostrar ayuda ----------------------------------------
    if not args.url:
        parser.print_help()
        sys.exit(0)

    # -- Verificar dependencias ----------------------------------------
    faltantes = verificar_dependencias()
    if faltantes:
        paquetes = ", ".join(faltantes)
        mostrar_error(
            f"Faltan dependencias: {paquetes}\n"
            f"         Instala con: sudo pacman -S {paquetes}"
        )
        sys.exit(1)

    # -- Cargar configuracion ------------------------------------------
    config = cargar_config(RUTA_AUTO)
    cfg_rutas = config.get("rutas", {})
    cfg_notif = config.get("notificacion", {})

    # -- Detectar plataforma -------------------------------------------
    plataforma = detectar_plataforma(args.url)

    # -- Resolver destino ----------------------------------------------
    destino = resolver_ruta_destino(plataforma, cfg_rutas)

    # -- Formato -------------------------------------------------------
    formato = obtener_formato()

    # -- Mostrar info --------------------------------------------------
    mostrar_info_descarga(args.url, plataforma, destino, args.playlist)

    logger.info(f"Descarga iniciada: {args.url} [{plataforma}, mp3]")

    # -- Descargar -----------------------------------------------------
    exito = descargar(args.url, destino, formato, args.playlist)

    if exito:
        print(f"\n  {_color('Descarga completada!', GREEN + BOLD)} → {destino}\n")
        logger.info(f"Descarga completada en {destino}")

        if not args.silent:
            import random
            mensajes_exito = [
                "¡Tu música está lista, disfruta!",
                "Descarga completada, ¡a escuchar!",
                "Cazado con éxito, ¡dale al play!",
            ]
            notificar(
                "cazador_medios",
                random.choice(mensajes_exito),
                cfg_notif.get("severidad", "exito"),
                cfg_notif.get("duracion", 5000),
            )
    else:
        mostrar_error("La descarga no se completo correctamente.")
        logger.error(f"Descarga fallida: {args.url}")

        if not args.silent:
            notificar(
                "cazador_medios",
                "La descarga fallo, revisa la URL o tu conexion.",
                "error",
                cfg_notif.get("duracion", 5000),
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
