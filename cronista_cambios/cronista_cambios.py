#!/usr/bin/env python3
"""
Cronista de Cambios — Generador de Changelogs
Genera CHANGELOG.md automaticamente desde el historial de git.
Parsea commits, los agrupa por tipo (feat, fix, refactor, etc.)
y genera formato Keep a Changelog. Sin LLM.
Parte del ecosistema: herramientas de desarrollo.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("cronista_cambios")

CONSEJERO = "cronista_cambios"

# -- Categorias ----------------------------------------------------------------

CATEGORIAS = {
    "feat": "Nuevas funcionalidades",
    "fix": "Correcciones",
    "refactor": "Cambios internos",
    "docs": "Documentacion",
    "test": "Tests",
    "style": "Estilo",
    "perf": "Rendimiento",
    "ci": "CI/CD",
    "chore": "Mantenimiento",
    "other": "Otros",
}

KEYWORDS = {
    "feat": ["add", "new", "feature", "implement", "añad", "nuevo", "crear"],
    "fix": ["fix", "bug", "patch", "correg", "arregl", "solucio"],
    "refactor": ["refactor", "clean", "move", "rename", "restructur", "reorganiz"],
    "docs": ["doc", "readme", "comment", "changelog"],
    "test": ["test", "spec", "coverage"],
    "style": ["style", "format", "lint", "indent"],
    "perf": ["perf", "optimi", "speed", "fast"],
    "ci": ["ci", "pipeline", "deploy", "docker", "github action"],
    "chore": ["chore", "bump", "update dep", "upgrade"],
}

# Patron para Conventional Commits: tipo(scope): mensaje  o  tipo: mensaje
_RE_CONVENTIONAL = re.compile(
    r"^(?P<tipo>[a-z]+)(?:\([^)]*\))?!?:\s*(?P<mensaje>.+)$", re.IGNORECASE
)

# Patrones de ruido a filtrar
_PATRONES_RUIDO = [
    re.compile(r"^Merge\s", re.IGNORECASE),
    re.compile(r"\bWIP\b", re.IGNORECASE),
    re.compile(r"^Initial commit$", re.IGNORECASE),
    re.compile(r"^Bump version", re.IGNORECASE),
    re.compile(r"^Auto-generated", re.IGNORECASE),
]


# -- Modelo de datos -----------------------------------------------------------

class Commit:
    """Representa un commit parseado."""

    __slots__ = ("hash", "mensaje", "autor", "fecha", "categoria", "descripcion")

    def __init__(self, hash: str, mensaje: str, autor: str, fecha: str):
        self.hash = hash
        self.mensaje = mensaje
        self.autor = autor
        self.fecha = fecha
        self.categoria = "other"
        self.descripcion = mensaje

    def __repr__(self) -> str:
        return f"Commit({self.hash[:8]}, {self.categoria}, {self.descripcion!r})"


# -- Funciones de git ----------------------------------------------------------

def verificar_repo_git() -> bool:
    """Verifica que el directorio actual sea un repositorio git."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def obtener_ultimo_tag() -> str | None:
    """Devuelve el ultimo tag del repositorio, o None si no hay tags."""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def obtener_nombre_repo() -> str:
    """Devuelve el nombre del repositorio actual."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).name
    return "repositorio"


def obtener_commits_raw(rango: str | None = None) -> tuple[bool, list[str]]:
    """Ejecuta git log y devuelve (ok, lineas_raw).

    Args:
        rango: Rango de refs (ej: "v1.0.0..v1.1.0", "v1.0.0..HEAD").
               Si es None, devuelve todo el historial.

    Returns:
        Tupla (ok, lineas).
        ok=False indica que git falló (ref/fecha no parseable o repo inválido).
        ok=True con lista vacía indica rango válido sin commits.
        ok=True con lista no vacía indica commits encontrados.
    """
    cmd = [
        "git", "log",
        "--pretty=format:%H|%s|%an|%ai",
    ]
    if rango:
        cmd.append(rango)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Error ejecutando git log: {result.stderr.strip()}")
        return False, []

    lineas = result.stdout.strip()
    if not lineas:
        return True, []
    return True, lineas.split("\n")


# -- Parseo y clasificacion ----------------------------------------------------

def es_ruido(mensaje: str) -> bool:
    """Determina si un commit es ruido que debe filtrarse."""
    for patron in _PATRONES_RUIDO:
        if patron.search(mensaje):
            return True
    # Commits que son solo "Co-Authored-By" sin contenido real
    limpio = re.sub(r"Co-Authored-By:.*", "", mensaje, flags=re.IGNORECASE).strip()
    if not limpio:
        return True
    return False


def clasificar_conventional(mensaje: str) -> tuple[str, str] | None:
    """Intenta parsear como Conventional Commit.

    Returns:
        Tupla (categoria, descripcion) o None si no es conventional.
    """
    match = _RE_CONVENTIONAL.match(mensaje)
    if not match:
        return None

    tipo = match.group("tipo").lower()
    descripcion = match.group("mensaje").strip()

    if tipo in CATEGORIAS:
        return tipo, descripcion

    # Tipos alternativos comunes
    alias = {
        "build": "chore",
        "revert": "fix",
        "breaking": "feat",
    }
    if tipo in alias:
        return alias[tipo], descripcion

    return None


def clasificar_por_keywords(mensaje: str) -> str:
    """Clasifica un commit por keywords en el mensaje.

    Returns:
        Categoria detectada, o "other" como fallback.
    """
    mensaje_lower = mensaje.lower()
    for categoria, palabras in KEYWORDS.items():
        for palabra in palabras:
            if palabra in mensaje_lower:
                return categoria
    return "other"


def parsear_commit(linea: str) -> Commit | None:
    """Parsea una linea raw de git log en un objeto Commit.

    Returns:
        Commit parseado o None si la linea es invalida.
    """
    partes = linea.split("|", 3)
    if len(partes) < 4:
        logger.warning(f"Linea de git log con formato inesperado: {linea!r}")
        return None

    hash_, mensaje, autor, fecha = partes
    commit = Commit(hash=hash_.strip(), mensaje=mensaje.strip(),
                    autor=autor.strip(), fecha=fecha.strip())
    return commit


def clasificar_commit(commit: Commit) -> None:
    """Clasifica un commit asignandole categoria y descripcion limpia.

    Modifica el commit in-place.
    """
    # Intentar Conventional Commits primero
    resultado = clasificar_conventional(commit.mensaje)
    if resultado:
        commit.categoria, commit.descripcion = resultado
        return

    # Fallback a keywords
    commit.categoria = clasificar_por_keywords(commit.mensaje)
    commit.descripcion = commit.mensaje


def procesar_commits(lineas_raw: list[str]) -> list[Commit]:
    """Parsea, filtra y clasifica una lista de commits raw.

    Returns:
        Lista de Commits listos para generar el changelog.
    """
    commits = []
    for linea in lineas_raw:
        if not linea.strip():
            continue

        commit = parsear_commit(linea)
        if commit is None:
            continue

        if es_ruido(commit.mensaje):
            logger.debug(f"Filtrado (ruido): {commit.mensaje}")
            continue

        clasificar_commit(commit)
        commits.append(commit)

    logger.info(f"Procesados {len(commits)} commits ({len(lineas_raw)} raw, "
                f"{len(lineas_raw) - len(commits)} filtrados)")
    return commits


# -- Agrupacion ----------------------------------------------------------------

def agrupar_por_categoria(commits: list[Commit]) -> dict[str, list[Commit]]:
    """Agrupa commits por su categoria manteniendo el orden definido.

    Returns:
        Dict ordenado {categoria: [commits]}, solo categorias con commits.
    """
    grupos: dict[str, list[Commit]] = {}
    for commit in commits:
        if commit.categoria not in grupos:
            grupos[commit.categoria] = []
        grupos[commit.categoria].append(commit)

    # Reordenar segun el orden de CATEGORIAS
    ordenado: dict[str, list[Commit]] = {}
    for cat in CATEGORIAS:
        if cat in grupos:
            ordenado[cat] = grupos[cat]
    return ordenado


# -- Generacion de salida ------------------------------------------------------

def generar_markdown(
    grupos: dict[str, list[Commit]],
    version: str | None = None,
    fecha: str | None = None,
    incluir_hash: bool = False,
    incluir_autor: bool = False,
) -> str:
    """Genera el changelog en formato Keep a Changelog.

    Args:
        grupos: Commits agrupados por categoria.
        version: Nombre de la version (ej: "v1.3.0"). Si None, usa "Sin version".
        fecha: Fecha de la version. Si None, usa la fecha actual.
        incluir_hash: Si True, añade el hash corto al final de cada linea.
        incluir_autor: Si True, añade el autor entre parentesis.

    Returns:
        String con el markdown generado.
    """
    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")

    etiqueta = version if version else "Sin version"
    lineas = [f"## [{etiqueta}] - {fecha}", ""]

    for categoria, commits in grupos.items():
        nombre_seccion = CATEGORIAS.get(categoria, "Otros")
        lineas.append(f"### {nombre_seccion}")
        for commit in commits:
            partes_linea = [f"- {commit.descripcion}"]
            if incluir_hash:
                partes_linea.append(f" (`{commit.hash[:7]}`)")
            if incluir_autor:
                partes_linea.append(f" — {commit.autor}")
            lineas.append("".join(partes_linea))
        lineas.append("")

    return "\n".join(lineas)


def generar_notas(grupos: dict[str, list[Commit]]) -> str:
    """Genera release notes en formato corto (sin headers markdown).

    Returns:
        String con las notas en formato plano.
    """
    lineas = []
    for categoria, commits in grupos.items():
        nombre_seccion = CATEGORIAS.get(categoria, "Otros")
        lineas.append(f"{nombre_seccion}:")
        for commit in commits:
            lineas.append(f"  - {commit.descripcion}")
        lineas.append("")

    return "\n".join(lineas)


def imprimir_color(
    grupos: dict[str, list[Commit]],
    version: str | None = None,
    fecha: str | None = None,
) -> None:
    """Imprime el changelog con colores ANSI en terminal.

    Args:
        grupos: Commits agrupados por categoria.
        version: Nombre de la version.
        fecha: Fecha de la version.
    """
    CYAN = "\033[36m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    if fecha is None:
        fecha = datetime.now().strftime("%Y-%m-%d")

    etiqueta = version if version else "Sin version"
    print(f"\n{BOLD}{CYAN}## [{etiqueta}] - {fecha}{RESET}\n")

    for categoria, commits in grupos.items():
        nombre_seccion = CATEGORIAS.get(categoria, "Otros")
        print(f"{CYAN}### {nombre_seccion}{RESET}")
        for commit in commits:
            print(f"  - {commit.descripcion}  {DIM}{commit.hash[:7]}{RESET}")
        print()


# -- Escritura de archivo ------------------------------------------------------

def escribir_changelog(
    ruta_archivo: Path,
    contenido_nuevo: str,
) -> None:
    """Escribe o actualiza CHANGELOG.md insertando el contenido nuevo al inicio.

    Si el archivo ya existe, inserta el nuevo contenido despues del titulo
    principal (linea que empieza con "# "). Si no existe, crea uno nuevo.
    """
    cabecera = "# Changelog\n\n"

    if ruta_archivo.exists():
        texto_existente = ruta_archivo.read_text(encoding="utf-8")
        # Buscar la primera linea de titulo "# Changelog" o similar
        lineas = texto_existente.split("\n")
        idx_insercion = 0
        for i, linea in enumerate(lineas):
            if linea.startswith("# "):
                idx_insercion = i + 1
                # Saltar lineas vacias despues del titulo
                while idx_insercion < len(lineas) and not lineas[idx_insercion].strip():
                    idx_insercion += 1
                break

        lineas_antes = lineas[:idx_insercion]
        lineas_despues = lineas[idx_insercion:]
        texto_final = "\n".join(lineas_antes) + "\n\n" + contenido_nuevo + "\n" + "\n".join(lineas_despues)
    else:
        texto_final = cabecera + contenido_nuevo + "\n"

    ruta_archivo.write_text(texto_final, encoding="utf-8")
    logger.info(f"Changelog escrito en {ruta_archivo}")


# -- Resolucion de rangos ------------------------------------------------------

def resolver_rango(argumento: str | None, desde: str | None) -> tuple[str | None, str | None]:
    """Resuelve el rango de commits a analizar.

    Args:
        argumento: Argumento posicional (puede ser "v1.0..v1.1" o None).
        desde: Valor de --desde.

    Returns:
        Tupla (rango_git, version_detectada).
        rango_git puede ser None (todo el historial).
        version_detectada es el extremo superior del rango si se puede deducir.
    """
    if argumento and ".." in argumento:
        # Rango explicito: v1.0.0..v1.1.0
        partes = argumento.split("..", 1)
        version = partes[1] if partes[1] != "HEAD" else None
        return argumento, version

    if desde:
        return f"{desde}..HEAD", None

    # Por defecto: desde ultimo tag hasta HEAD
    ultimo_tag = obtener_ultimo_tag()
    if ultimo_tag:
        return f"{ultimo_tag}..HEAD", None

    # Sin tags: todo el historial
    return None, None


# -- CLI -----------------------------------------------------------------------

def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos CLI."""
    parser = argparse.ArgumentParser(
        prog="changelog",
        description="Genera CHANGELOG.md desde el historial de git",
    )
    parser.add_argument(
        "rango",
        nargs="?",
        default=None,
        help="Rango de refs (ej: v1.0.0..v1.1.0). Por defecto: ultimo tag..HEAD",
    )
    parser.add_argument(
        "--desde",
        default=None,
        help="Generar desde un ref hasta HEAD",
    )
    parser.add_argument(
        "--archivo",
        action="store_true",
        help="Escribir/actualizar CHANGELOG.md en el repositorio",
    )
    parser.add_argument(
        "--notas",
        action="store_true",
        help="Formato corto para release notes (sin headers markdown)",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        dest="incluir_hash",
        help="Incluir hash corto en cada linea",
    )
    parser.add_argument(
        "--autor",
        action="store_true",
        dest="incluir_autor",
        help="Incluir nombre del autor en cada linea",
    )
    return parser


def main(args: list[str] | None = None) -> int:
    """Punto de entrada principal.

    Args:
        args: Lista de argumentos (para testing). Si None, usa sys.argv.

    Returns:
        Codigo de salida (0 exito, 1 error).
    """
    parser = construir_parser()
    opts = parser.parse_args(args)

    # Verificar que estamos en un repo git
    if not verificar_repo_git():
        logger.error("No se encuentra un repositorio git en el directorio actual")
        print("Error: no se encuentra un repositorio git en el directorio actual",
              file=sys.stderr)
        return 1

    # Cargar configuracion
    config = cargar_config(RUTA_AUTO)
    cfg_changelog = config.get("changelog", {})
    cfg_notif = config.get("notificacion", {})

    incluir_hash = opts.incluir_hash or cfg_changelog.get("incluir_hash", False)
    incluir_autor = opts.incluir_autor or cfg_changelog.get("incluir_autor", False)

    # Resolver rango
    rango, version_detectada = resolver_rango(opts.rango, opts.desde)
    logger.info(f"Rango: {rango or 'todo el historial'}")

    # Obtener y procesar commits
    git_ok, lineas_raw = obtener_commits_raw(rango)
    if not git_ok:
        # git falló: ref/fecha inválida o repo con problemas
        ref_mostrada = opts.desde or opts.rango or rango or "(desconocido)"
        mensaje = f"Referencia o fecha inválida: {ref_mostrada}"
        logger.error(mensaje)
        print(f"Error: {mensaje}", file=sys.stderr)
        return 1
    if not lineas_raw:
        mensaje = "No se encontraron commits en el rango especificado"
        logger.info(mensaje)
        print(mensaje)
        notificar(CONSEJERO, mensaje,
                  severidad=cfg_notif.get("severidad", "info"),
                  duracion=cfg_notif.get("duracion", 3000))
        return 0

    commits = procesar_commits(lineas_raw)
    if not commits:
        mensaje = "Todos los commits fueron filtrados (ruido)"
        logger.info(mensaje)
        print(mensaje)
        return 0

    grupos = agrupar_por_categoria(commits)

    # Determinar version y fecha
    version = version_detectada
    if not version and rango and ".." in rango:
        partes = rango.split("..", 1)
        if partes[1] != "HEAD":
            version = partes[1]

    fecha = datetime.now().strftime("%Y-%m-%d")
    nombre_repo = obtener_nombre_repo()

    # Generar salida
    if opts.notas:
        texto = generar_notas(grupos)
        print(texto)
    elif opts.archivo:
        md = generar_markdown(grupos, version=version, fecha=fecha,
                              incluir_hash=incluir_hash, incluir_autor=incluir_autor)
        # Buscar la raiz del repo git
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error("No se pudo determinar la raiz del repositorio")
            return 1
        ruta_repo = Path(result.stdout.strip())
        ruta_changelog = ruta_repo / "CHANGELOG.md"
        escribir_changelog(ruta_changelog, md)

        mensaje = f"CHANGELOG.md actualizado en {nombre_repo} ({len(commits)} commits)"
        print(f"Escrito: {ruta_changelog}")
        notificar(CONSEJERO, mensaje,
                  severidad="exito",
                  duracion=cfg_notif.get("duracion", 3000))
    else:
        # Salida por terminal con colores
        imprimir_color(grupos, version=version, fecha=fecha)

    logger.info(f"Changelog generado: {len(commits)} commits en "
                f"{len(grupos)} categorias")
    return 0


if __name__ == "__main__":
    sys.exit(main())
