#!/usr/bin/env python3
"""
Explorador de Archivos — Buscador Universal
Busca en archivos, contenido, historial y logs con un solo comando.
Parte del ecosistema: herramientas bajo demanda.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("explorador_archivos")

# ── Colores ANSI ─────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
WHITE = "\033[37m"

# Colores por categoría
COLOR_ARCHIVOS = CYAN
COLOR_CONTENIDO = GREEN
COLOR_HISTORIAL = YELLOW
COLOR_LOGS = MAGENTA


def _color(texto: str, color: str) -> str:
    """Envuelve texto con código de color ANSI."""
    return f"{color}{texto}{RESET}"


# ── Verificación de herramientas ─────────────────────────────────


def verificar_herramienta(nombre: str) -> bool:
    """Comprueba que una herramienta está instalada."""
    return shutil.which(nombre) is not None


def verificar_dependencias() -> list[str]:
    """Verifica que fd y rg estén disponibles. Devuelve lista de ausentes."""
    ausentes = []
    if not verificar_herramienta("fd"):
        ausentes.append("fd")
    if not verificar_herramienta("rg"):
        ausentes.append("rg (ripgrep)")
    return ausentes


# ── Funciones de búsqueda ────────────────────────────────────────


def buscar_archivos(termino: str, directorio: str, excluir: list[str],
                    limite: int, timeout: int) -> list[str]:
    """
    Busca archivos por nombre usando fd.

    Args:
        termino: Texto a buscar en nombres de archivo.
        directorio: Directorio base de búsqueda.
        excluir: Lista de directorios/patrones a excluir.
        limite: Máximo de resultados (0 = sin límite).
        timeout: Timeout en segundos.

    Returns:
        Lista de rutas de archivos encontrados.
    """
    cmd = ["fd", "--color", "never", "--type", "f"]
    for patron in excluir:
        cmd.extend(["--exclude", patron])
    cmd.extend([termino, directorio])
    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lineas = [l for l in resultado.stdout.strip().splitlines() if l]
        if limite > 0:
            lineas = lineas[:limite]
        return lineas
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout buscando archivos por nombre ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.error("Comando fd no encontrado")
        return []


def buscar_contenido(termino: str, directorio: str, excluir: list[str],
                     limite: int, timeout: int,
                     excluir_rutas: list[str] | None = None) -> list[str]:
    """
    Busca dentro del contenido de archivos usando ripgrep.

    Args:
        termino: Texto a buscar dentro de archivos.
        directorio: Directorio base de búsqueda.
        excluir: Lista de patrones de nombre (directorios/globs) a excluir vía --glob.
                 Solo para patrones de nombre, NO rutas absolutas (rg las ignora).
        limite: Máximo de resultados (0 = sin límite).
        timeout: Timeout en segundos.
        excluir_rutas: Rutas absolutas de directorios o ficheros a excluir
                       filtrando el resultado post-búsqueda (rg no soporta rutas
                       absolutas en --glob).

    Returns:
        Lista de coincidencias en formato archivo:linea: texto.
    """
    cmd = [
        "rg",
        "--color", "never",
        "--no-heading",
        "--line-number",
        "--smart-case",
        "--max-columns", "200",
        "--max-columns-preview",
    ]
    for patron in excluir:
        # Solo añadir patrones de nombre (no rutas absolutas; rg las ignora en --glob)
        if not patron.startswith("/"):
            cmd.extend(["--glob", f"!{patron}"])
    if limite > 0:
        cmd.extend(["--max-count", str(limite)])
    cmd.extend(["--", termino, directorio])

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lineas = [l for l in resultado.stdout.strip().splitlines() if l]
        # Filtrar post-búsqueda las rutas absolutas excluidas (rg no soporta
        # rutas absolutas en --glob, así que la exclusión la hacemos aquí).
        if excluir_rutas:
            rutas_norm = [r.rstrip("/") for r in excluir_rutas]
            lineas = [
                l for l in lineas
                if not any(
                    l.startswith(ruta + ":") or l.startswith(ruta + "/")
                    for ruta in rutas_norm
                )
            ]
        if limite > 0:
            lineas = lineas[:limite]
        return lineas
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout buscando en contenido ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.error("Comando rg no encontrado")
        return []


def buscar_historial(termino: str, ruta_historial: str,
                     limite: int) -> list[str]:
    """
    Busca en el historial de terminal.

    Args:
        termino: Texto a buscar.
        ruta_historial: Ruta al fichero de historial.
        limite: Máximo de resultados (0 = sin límite).

    Returns:
        Lista de comandos que coinciden.
    """
    ruta = Path(ruta_historial).expanduser()
    if not ruta.exists():
        logger.warning(f"Historial no encontrado: {ruta}")
        return []

    try:
        contenido = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.error(f"Error leyendo historial: {e}")
        return []

    termino_lower = termino.lower()
    coincidencias = []
    vistas = set()

    for linea in reversed(contenido.splitlines()):
        linea_limpia = linea.strip()
        if not linea_limpia:
            continue
        if termino_lower in linea_limpia.lower() and linea_limpia not in vistas:
            coincidencias.append(linea_limpia)
            vistas.add(linea_limpia)
            if limite > 0 and len(coincidencias) >= limite:
                break

    return coincidencias


def buscar_logs_excluyendo(termino: str, directorio_logs: str,
                            excluir_archivos: list[str],
                            limite: int, timeout: int) -> list[str]:
    """
    Busca en los logs de automatizaciones usando ripgrep, excluyendo archivos concretos.

    Args:
        termino: Texto a buscar.
        directorio_logs: Directorio donde están los logs.
        excluir_archivos: Lista de rutas absolutas de archivos a excluir.
                          La exclusión se aplica filtrando el resultado post-búsqueda,
                          porque rg ignora rutas absolutas en --glob.
        limite: Máximo de resultados (0 = sin límite).
        timeout: Timeout en segundos.

    Returns:
        Lista de coincidencias en formato archivo:linea: texto.
    """
    ruta = Path(directorio_logs)
    if not ruta.exists():
        logger.warning(f"Directorio de logs no encontrado: {ruta}")
        return []

    cmd = [
        "rg",
        "--color", "never",
        "--no-heading",
        "--line-number",
        "--smart-case",
        "--max-columns", "200",
        "--max-columns-preview",
    ]
    if limite > 0:
        cmd.extend(["--max-count", str(limite)])
    cmd.extend(["--", termino, str(ruta)])

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lineas = [l for l in resultado.stdout.strip().splitlines() if l]
        # Filtrar post-búsqueda los archivos excluidos. rg no soporta rutas
        # absolutas en --glob (las ignora silenciosamente), así que aplicamos
        # la exclusión aquí comparando el prefijo de ruta de cada línea.
        if excluir_archivos:
            excluidos_norm = [str(Path(f).resolve()) for f in excluir_archivos]
            lineas = [
                l for l in lineas
                if not any(l.startswith(excluido + ":") for excluido in excluidos_norm)
            ]
        if limite > 0:
            lineas = lineas[:limite]
        return lineas
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout buscando en logs ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.error("Comando rg no encontrado")
        return []


def buscar_logs(termino: str, directorio_logs: str, limite: int,
                timeout: int) -> list[str]:
    """
    Busca en los logs de automatizaciones usando ripgrep.

    Args:
        termino: Texto a buscar.
        directorio_logs: Directorio donde están los logs.
        limite: Máximo de resultados (0 = sin límite).
        timeout: Timeout en segundos.

    Returns:
        Lista de coincidencias en formato archivo:linea: texto.
    """
    ruta = Path(directorio_logs)
    if not ruta.exists():
        logger.warning(f"Directorio de logs no encontrado: {ruta}")
        return []

    cmd = [
        "rg",
        "--color", "never",
        "--no-heading",
        "--line-number",
        "--smart-case",
        "--max-columns", "200",
        "--max-columns-preview",
    ]
    if limite > 0:
        cmd.extend(["--max-count", str(limite)])
    cmd.extend(["--", termino, str(ruta)])

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        lineas = [l for l in resultado.stdout.strip().splitlines() if l]
        if limite > 0:
            lineas = lineas[:limite]
        return lineas
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout buscando en logs ({timeout}s)")
        return []
    except FileNotFoundError:
        logger.error("Comando rg no encontrado")
        return []


# ── Formato de salida ────────────────────────────────────────────


def _abreviar_home(ruta: str) -> str:
    """Sustituye /home/usuario por ~ para rutas más legibles."""
    home = str(Path.home())
    if ruta.startswith(home):
        return "~" + ruta[len(home):]
    return ruta


def mostrar_categoria(nombre: str, resultados: list[str], color: str,
                      abreviar: bool = True):
    """
    Muestra una categoría de resultados con formato.

    Args:
        nombre: Nombre de la categoría.
        resultados: Lista de líneas a mostrar.
        color: Código de color ANSI para el header.
        abreviar: Si True, sustituye la ruta home por ~.
    """
    n = len(resultados)
    etiqueta = "resultado" if n == 1 else "resultados"
    header = f" {nombre} ({n} {etiqueta}) "
    ancho = 50
    relleno = max(0, ancho - len(header))

    print()
    print(f"  {_color('───', DIM)}{_color(header, color + BOLD)}{_color('─' * relleno, DIM)}")

    if not resultados:
        print(f"    {_color('Sin resultados', DIM)}")
        return

    for linea in resultados:
        texto = _abreviar_home(linea) if abreviar else linea
        print(f"    {texto}")


def mostrar_resumen(total: int, termino: str):
    """Muestra un resumen final con el total de resultados."""
    print()
    if total == 0:
        print(f"  {_color('No se encontraron resultados para', DIM)} {_color(termino, RED + BOLD)}")
    else:
        etiqueta = "resultado" if total == 1 else "resultados"
        print(f"  {_color(f'{total} {etiqueta} encontrados para', DIM)} {_color(termino, CYAN + BOLD)}")
    print()


def mostrar_error(mensaje: str):
    """Muestra un mensaje de error con formato."""
    print(f"\n  {_color('Error:', RED + BOLD)} {mensaje}\n", file=sys.stderr)


# ── CLI ──────────────────────────────────────────────────────────


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="buscar",
        description="Buscador universal: archivos, contenido, historial y logs.",
        epilog=(
            "Ejemplos:\n"
            '  buscar openai              Busca en todas las categorías\n'
            '  buscar "mi proyecto" -a    Solo nombres de archivo\n'
            '  buscar error --logs        Solo en logs de automatizaciones\n'
            '  buscar pip --historial     Solo en historial de terminal\n'
            '  buscar config -c           Solo dentro del contenido\n'
            '  buscar todo --limit 5      Limita a 5 resultados por categoría\n'
            '  buscar python --todo       Sin límite de resultados\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "termino",
        nargs="+",
        help="Término de búsqueda",
    )

    filtros = parser.add_argument_group("filtros de categoría")
    filtros.add_argument(
        "--archivos", "-a",
        action="store_true",
        help="Buscar solo en nombres de archivo",
    )
    filtros.add_argument(
        "--contenido", "-c",
        action="store_true",
        help="Buscar solo en contenido de archivos",
    )
    filtros.add_argument(
        "--historial", "-H",
        action="store_true",
        help="Buscar solo en historial de terminal",
    )
    filtros.add_argument(
        "--logs", "-l",
        action="store_true",
        help="Buscar solo en logs de automatizaciones",
    )

    opciones = parser.add_argument_group("opciones")
    opciones.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Máximo de resultados por categoría",
    )
    opciones.add_argument(
        "--todo",
        action="store_true",
        help="Sin límite de resultados",
    )

    return parser


def es_ruta(texto: str) -> bool:
    """Detecta si el texto parece una ruta del sistema."""
    return texto.startswith(("/", "~/", "./"))


def abrir_ruta(ruta: str):
    """Abre una ruta con xdg-open (carpeta o archivo)."""
    path = Path(ruta).expanduser().resolve()
    if not path.exists():
        mostrar_error(f"La ruta no existe: {path}")
        sys.exit(1)
    print(f"\n  {_color('Abriendo:', CYAN + BOLD)} {path}\n")
    subprocess.run(["xdg-open", str(path)], capture_output=True)


def main():
    parser = construir_parser()
    args = parser.parse_args()

    # ── Detectar si es una ruta y abrirla directamente ──────────
    termino_raw = " ".join(args.termino)
    if es_ruta(termino_raw):
        abrir_ruta(termino_raw)
        return

    # ── Verificar dependencias ───────────────────────────────────
    ausentes = verificar_dependencias()
    if ausentes:
        mostrar_error(
            "Herramientas necesarias no instaladas: "
            + ", ".join(ausentes)
            + "\n         Instálalas con: sudo pacman -S fd ripgrep"
        )
        sys.exit(1)

    # ── Cargar configuración ─────────────────────────────────────
    config = cargar_config(RUTA_AUTO)
    cfg_busq = config.get("busqueda", {})

    termino = " ".join(args.termino)
    directorio = str(Path(cfg_busq.get("directorio_base", "~")).expanduser())
    directorio_logs = cfg_busq.get(
        "directorio_logs",
        "/home/sun/Escritorio/automatizaciones/logs",
    )
    historial = cfg_busq.get("historial", "~/.bash_history")
    excluir = cfg_busq.get("excluir", [
        ".cache", "node_modules", ".git", "__pycache__", ".venv", "venv",
    ])
    timeout = cfg_busq.get("timeout", 15)

    # ── Resolver límite ──────────────────────────────────────────
    if args.todo:
        limite = 0
    elif args.limit is not None:
        limite = args.limit
    else:
        limite = cfg_busq.get("limite_por_categoria", 10)

    # ── Determinar categorías a buscar ───────────────────────────
    filtro_activo = args.archivos or args.contenido or args.historial or args.logs
    buscar_en_archivos = args.archivos or not filtro_activo
    buscar_en_contenido = args.contenido or not filtro_activo
    buscar_en_historial = args.historial or not filtro_activo
    buscar_en_logs = args.logs or not filtro_activo

    logger.info(f"Buscando '{termino}' (limite={limite})")

    # ── Ejecutar búsquedas ───────────────────────────────────────
    total = 0

    # En búsqueda general (sin --logs) excluir el directorio de logs completo
    # para evitar que aparezcan resultados duplicados o que el log propio
    # contamine los resultados de archivos/contenido.
    excluir_general = list(excluir)
    if not args.logs and directorio_logs:
        excluir_general.append(directorio_logs)

    if buscar_en_archivos:
        resultados = buscar_archivos(termino, directorio, excluir_general, limite, timeout)
        mostrar_categoria("Archivos", resultados, COLOR_ARCHIVOS)
        total += len(resultados)

    if buscar_en_contenido:
        # excluir_general puede contener la ruta absoluta del dir de logs;
        # se pasa como excluir_rutas (filtrado post-búsqueda) porque rg
        # ignora rutas absolutas en --glob.
        rutas_excluidas = [p for p in excluir_general if p.startswith("/")]
        patrones_excluidos = [p for p in excluir_general if not p.startswith("/")]
        resultados = buscar_contenido(
            termino, directorio, patrones_excluidos, limite, timeout,
            excluir_rutas=rutas_excluidas,
        )
        mostrar_categoria("Contenido", resultados, COLOR_CONTENIDO)
        total += len(resultados)

    if buscar_en_historial:
        resultados = buscar_historial(termino, historial, limite)
        mostrar_categoria("Historial", resultados, COLOR_HISTORIAL,
                          abreviar=False)
        total += len(resultados)

    if buscar_en_logs:
        # En modo --logs excluir solo el log propio para no contaminar
        # los resultados de los demás logs.
        log_propio = str(Path(directorio_logs) / "explorador_archivos.log")
        resultados = buscar_logs_excluyendo(termino, directorio_logs, [log_propio], limite, timeout)
        mostrar_categoria("Logs", resultados, COLOR_LOGS)
        total += len(resultados)

    # ── Resumen ──────────────────────────────────────────────────
    mostrar_resumen(total, termino)

    logger.info(f"Búsqueda completada: '{termino}' — {total} resultados")


if __name__ == "__main__":
    main()
