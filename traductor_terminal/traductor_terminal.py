#!/usr/bin/env python3
"""
Traductor de Terminal
Traduce texto al instante sin salir de la terminal.
Parte del ecosistema: herramientas bajo demanda.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("traductor_terminal")

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


# ── Lista blanca de idiomas ISO 639-1 ────────────────────────────
# Códigos aceptados por translate-shell. Lista extendida de los más comunes.
_IDIOMAS_VALIDOS: frozenset[str] = frozenset({
    "af", "sq", "am", "ar", "hy", "az", "eu", "be", "bn", "bs",
    "bg", "ca", "ceb", "ny", "zh", "zh-CN", "zh-TW", "co", "hr",
    "cs", "da", "nl", "en", "eo", "et", "tl", "fi", "fr", "fy",
    "gl", "ka", "de", "el", "gu", "ht", "ha", "haw", "he", "iw",
    "hi", "hmn", "hu", "is", "ig", "id", "ga", "it", "ja", "jw",
    "kn", "kk", "km", "ko", "ku", "ky", "lo", "la", "lv", "lt",
    "lb", "mk", "mg", "ms", "ml", "mt", "mi", "mr", "mn", "my",
    "ne", "no", "ps", "fa", "pl", "pt", "pa", "ro", "ru", "sm",
    "gd", "sr", "st", "sn", "sd", "si", "sk", "sl", "so", "es",
    "su", "sw", "sv", "tg", "ta", "te", "th", "tr", "uk", "ur",
    "uz", "vi", "cy", "xh", "yi", "yo", "zu",
})

_EJEMPLOS_IDIOMAS = "es, en, fr, de, pt, it, ja, zh, ar, ru"


def validar_idioma(codigo: str) -> bool:
    """Comprueba si el código ISO de idioma es conocido y válido."""
    return codigo in _IDIOMAS_VALIDOS


# ── Funciones de sistema ─────────────────────────────────────────


def verificar_trans() -> bool:
    """Comprueba que translate-shell (trans) está instalado."""
    return shutil.which("trans") is not None


def leer_portapapeles() -> str | None:
    """Lee el contenido del portapapeles con wl-paste."""
    if not shutil.which("wl-paste"):
        logger.error("wl-paste no encontrado (instalar wl-clipboard)")
        return None
    try:
        resultado = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if resultado.returncode != 0:
            logger.error(f"wl-paste falló: {resultado.stderr.strip()}")
            return None
        contenido = resultado.stdout.strip()
        return contenido if contenido else None
    except subprocess.TimeoutExpired:
        logger.error("Timeout al leer portapapeles")
        return None


def copiar_portapapeles(texto: str) -> bool:
    """Copia texto al portapapeles con wl-copy."""
    if not shutil.which("wl-copy"):
        logger.error("wl-copy no encontrado (instalar wl-clipboard)")
        return False
    try:
        resultado = subprocess.run(
            ["wl-copy", "--", texto],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return resultado.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("Timeout al copiar al portapapeles")
        return False


# ── Traducción ───────────────────────────────────────────────────


def traducir(texto: str, idioma_destino: str = "es",
             motor: str = "google", timeout: int = 10) -> str | None:
    """
    Traduce texto usando translate-shell.

    Args:
        texto: Texto a traducir.
        idioma_destino: Código ISO 639-1 del idioma destino.
        motor: Motor de traducción (google, bing, yandex).
        timeout: Timeout en segundos.

    Returns:
        Texto traducido o None si hay error.
    """
    cmd = [
        "trans",
        "-b",                   # solo traducción, sin decoración
        "-e", motor,            # motor de traducción
        f":{idioma_destino}",   # idioma destino (auto-detecta origen)
        "--", texto,
    ]

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if resultado.returncode != 0:
            error = resultado.stderr.strip()
            logger.error(f"trans falló (rc={resultado.returncode}): {error}")
            return None
        traduccion = resultado.stdout.strip()
        return traduccion if traduccion else None
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout ({timeout}s) al traducir")
        return None
    except FileNotFoundError:
        logger.error("Comando trans no encontrado")
        return None


def detectar_idioma(texto: str, motor: str = "google",
                    timeout: int = 10) -> str | None:
    """
    Detecta el idioma del texto usando translate-shell.

    Returns:
        Código del idioma detectado o None.
    """
    cmd = [
        "trans",
        "-id",                  # solo identificación de idioma
        "-e", motor,
        "-b",
        "--no-warn",
        "--", texto,
    ]
    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # trans -id -b devuelve el nombre del idioma
        salida = resultado.stdout.strip()
        return salida if salida else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ── Interfaz de terminal ─────────────────────────────────────────


def mostrar_resultado(texto_original: str, traduccion: str,
                      idioma_destino: str, copiado: bool = False):
    """Muestra el resultado con colores en terminal."""
    print()
    print(f"  {_color('Original:', DIM)}  {texto_original}")
    print(f"  {_color(f'[{idioma_destino}]', CYAN)}       {_color(BOLD, '')}{_color(traduccion, GREEN)}")
    if copiado:
        print(f"  {_color('Copiado al portapapeles', DIM + MAGENTA)}")
    print()


def mostrar_error(mensaje: str):
    """Muestra un mensaje de error con formato."""
    print(f"\n  {_color('Error:', RED + BOLD)} {mensaje}\n", file=sys.stderr)


# ── CLI ──────────────────────────────────────────────────────────


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="trad",
        description="Traduce texto al instante desde la terminal.",
        epilog=(
            "Ejemplos:\n"
            '  trad "hello world"          Traduce al español\n'
            '  trad "bonjour" --to en      Traduce al inglés\n'
            "  trad -c                     Traduce el portapapeles\n"
            "  trad -c -C                  Portapapeles → traduce → copia\n"
            '  trad "ciao" --to en --copy  Traduce y copia resultado\n'
            "  trad -i                     Modo interactivo (REPL)\n"
            "  trad -i --to en             REPL traduciendo al inglés\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "texto",
        nargs="*",
        help="Texto a traducir",
    )
    parser.add_argument(
        "--to", "-t",
        dest="idioma",
        metavar="IDIOMA",
        help="Idioma destino (código ISO: en, fr, de, pt, it, ja...)",
    )
    parser.add_argument(
        "--clipboard", "-c",
        action="store_true",
        help="Leer texto del portapapeles",
    )
    parser.add_argument(
        "--copy", "-C",
        action="store_true",
        help="Copiar traducción al portapapeles",
    )
    parser.add_argument(
        "--silent", "-s",
        action="store_true",
        help="Sin notificación de escritorio",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Modo interactivo REPL (Ctrl+D para salir)",
    )
    return parser


def modo_interactivo(idioma_destino: str, motor: str, timeout: int,
                     copiar: bool):
    """REPL interactivo para traducir múltiples frases seguidas."""
    print()
    print(f"  {_color('Traductor interactivo', BOLD + CYAN)} → {_color(idioma_destino, GREEN)}")
    print(f"  {_color('Escribe texto para traducir. Ctrl+D o «salir» para terminar.', DIM)}")
    print()

    contador = 0
    while True:
        try:
            linea = input(f"  {_color('>', CYAN)} ")
        except (EOFError, KeyboardInterrupt):
            break

        if not linea.strip():
            continue
        if linea.strip().lower() in ("salir", "exit", "quit", "q"):
            break

        traduccion = traducir(linea.strip(), idioma_destino, motor, timeout)
        if traduccion:
            copiado = False
            if copiar:
                copiado = copiar_portapapeles(traduccion)
            print(f"    {_color(traduccion, GREEN)}", end="")
            if copiado:
                print(f"  {_color('(copiado)', DIM + MAGENTA)}", end="")
            print()
            contador += 1
        else:
            print(f"    {_color('No se pudo traducir', RED)}")

    print()
    print(f"  {_color(f'{contador} traducciones realizadas.', DIM)}")
    print()


def main():
    parser = construir_parser()
    args = parser.parse_args()

    # ── Verificar dependencia ────────────────────────────────────
    if not verificar_trans():
        mostrar_error(
            "translate-shell no está instalado.\n"
            "         Instálalo con: sudo pacman -S translate-shell"
        )
        sys.exit(1)

    # ── Cargar configuración ─────────────────────────────────────
    config = cargar_config(RUTA_AUTO)
    cfg_trad = config.get("traduccion", {})
    cfg_notif = config.get("notificacion", {})
    cfg_clip = config.get("portapapeles", {})

    idioma_destino = args.idioma or cfg_trad.get("idioma_destino", "es")
    motor = cfg_trad.get("motor", "google")
    timeout = cfg_trad.get("timeout", 10)
    auto_copiar = cfg_clip.get("auto_copiar", False)

    # ── Validar código de idioma ─────────────────────────────────
    if not validar_idioma(idioma_destino):
        mostrar_error(
            f"Idioma inválido: '{idioma_destino}'\n"
            f"         Ejemplos válidos: {_EJEMPLOS_IDIOMAS}\n"
            f"         Consulta la lista completa en la documentación de translate-shell."
        )
        sys.exit(1)

    # ── Modo interactivo ─────────────────────────────────────────
    if args.interactive:
        modo_interactivo(idioma_destino, motor, timeout,
                         args.copy or auto_copiar)
        return

    # ── Obtener texto ────────────────────────────────────────────
    if args.clipboard:
        texto = leer_portapapeles()
        if not texto:
            mostrar_error("El portapapeles está vacío o no se pudo leer.")
            sys.exit(1)
        logger.info(f"Texto leído del portapapeles ({len(texto)} chars)")
    elif args.texto:
        texto = " ".join(args.texto)
    else:
        parser.print_help()
        sys.exit(0)

    if not texto.strip():
        mostrar_error("No se proporcionó texto para traducir.")
        sys.exit(1)

    # ── Traducir ─────────────────────────────────────────────────
    logger.info(f"Traduciendo a '{idioma_destino}' ({motor}): {texto[:80]}...")

    traduccion = traducir(texto, idioma_destino, motor, timeout)

    if not traduccion:
        mostrar_error("No se pudo obtener la traducción.")
        if not args.silent:
            notificar(
                "traductor_terminal",
                "Error al traducir el texto",
                "error",
                cfg_notif.get("duracion", 4000),
            )
        sys.exit(1)

    # ── Portapapeles ─────────────────────────────────────────────
    copiar = args.copy or auto_copiar
    copiado = False
    if copiar:
        copiado = copiar_portapapeles(traduccion)
        if copiado:
            logger.info("Traducción copiada al portapapeles")
        else:
            logger.warning("No se pudo copiar al portapapeles")

    # ── Mostrar resultado ────────────────────────────────────────
    mostrar_resultado(texto, traduccion, idioma_destino, copiado)

    # ── Notificación ─────────────────────────────────────────────
    if not args.silent:
        notificar(
            "traductor_terminal",
            "Traducción lista, ¡espero que te sirva!",
            cfg_notif.get("severidad", "exito"),
            cfg_notif.get("duracion", 5000),
        )

    logger.info(f"Traducción completada: '{texto[:40]}' → '{traduccion[:40]}'")


if __name__ == "__main__":
    main()
