#!/usr/bin/env python3
"""
Explorador de Feeds
Lector de RSS — descarga articulos de blogs y noticias, opcionalmente los resume con LLM.
Parte del ecosistema: herramientas bajo demanda + timer diario.
"""

import argparse
import json
import sys
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config
from comun import consultar_llm, llm_disponible

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_FUENTES = RUTA_AUTO / "fuentes.toml"

logger = configurar_logger("explorador_feeds")

# -- Colores ANSI -----------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"


def _color(texto: str, color: str) -> str:
    """Envuelve texto con codigo de color ANSI."""
    return f"{color}{texto}{RESET}"


# -- Gestion de fuentes -----------------------------------------------


def cargar_fuentes() -> list[dict]:
    """Carga las fuentes RSS desde fuentes.toml.

    Returns:
        Lista de diccionarios con nombre, url, categoria.
    """
    if not RUTA_FUENTES.exists():
        return []
    with open(RUTA_FUENTES, "rb") as f:
        datos = tomllib.load(f)
    return datos.get("fuente", [])


def guardar_fuentes(fuentes: list[dict]) -> None:
    """Guarda las fuentes RSS en fuentes.toml.

    Args:
        fuentes: Lista de diccionarios con nombre, url, categoria.
    """
    lineas = []
    for fuente in fuentes:
        lineas.append("[[fuente]]")
        lineas.append(f'nombre = "{fuente["nombre"]}"')
        lineas.append(f'url = "{fuente["url"]}"')
        lineas.append(f'categoria = "{fuente.get("categoria", "general")}"')
        lineas.append("")
    RUTA_FUENTES.write_text("\n".join(lineas), encoding="utf-8")


def añadir_fuente(url: str, nombre: str | None = None, categoria: str = "general") -> str:
    """Anade una nueva fuente RSS.

    Args:
        url: URL del feed RSS/Atom. Debe tener esquema http(s) y dominio no vacío.
        nombre: Nombre descriptivo (si None, se usa el dominio).
        categoria: Categoria de la fuente.

    Returns:
        Nombre asignado a la fuente.

    Raises:
        SystemExit: si la URL no tiene esquema http(s) o dominio válido.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        logger.error("URL inválida (esquema '%s'): %s", parsed.scheme, url)
        print(
            f"Error: la URL debe empezar por http:// o https://\n"
            f"  Recibido: {url}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not parsed.netloc:
        logger.error("URL inválida (dominio vacío): %s", url)
        print(
            f"Error: la URL no tiene dominio válido\n"
            f"  Recibido: {url}",
            file=sys.stderr,
        )
        sys.exit(1)

    fuentes = cargar_fuentes()

    # Comprobar duplicados
    for f in fuentes:
        if f["url"] == url:
            return f["nombre"]

    if not nombre:
        dominio = parsed.netloc
        nombre = dominio.replace("www.", "").split(".")[0].title()
        if not nombre:
            logger.error("No se pudo derivar un nombre de la URL: %s", url)
            print(f"Error: no se pudo derivar un nombre de la URL: {url}", file=sys.stderr)
            sys.exit(1)

    fuentes.append({"nombre": nombre, "url": url, "categoria": categoria})
    guardar_fuentes(fuentes)
    return nombre


def borrar_fuente(nombre: str) -> bool:
    """Elimina una fuente RSS por nombre.

    Args:
        nombre: Nombre de la fuente (coincidencia exacta, sin distinguir mayusculas).
                Un nombre vacío se rechaza con error para evitar borrar todo.

    Returns:
        True si se elimino alguna fuente.

    Raises:
        SystemExit: si nombre está vacío.
    """
    if not nombre.strip():
        logger.error("Nombre de fuente vacío — borrado rechazado")
        print(
            "Error: el nombre de fuente no puede estar vacío.\n"
            "  Usa 'feeds --lista' para ver los nombres disponibles.",
            file=sys.stderr,
        )
        sys.exit(1)

    fuentes = cargar_fuentes()
    nombre_lower = nombre.strip().lower()
    # Coincidencia exacta (insensible a mayúsculas) en lugar de substring
    # para evitar que un término corto borre múltiples fuentes.
    nuevas = [f for f in fuentes if f["nombre"].lower() != nombre_lower]
    if len(nuevas) == len(fuentes):
        return False
    guardar_fuentes(nuevas)
    return True


# -- Parseo RSS/Atom ---------------------------------------------------

_NS_ATOM = "{http://www.w3.org/2005/Atom}"


def _parsear_fecha(texto: str | None) -> str:
    """Intenta parsear una fecha de RSS o Atom y devuelve formato ISO.

    Args:
        texto: Texto de la fecha en formato RFC 2822 o ISO 8601.

    Returns:
        Fecha en formato ISO o cadena vacia si no se puede parsear.
    """
    if not texto:
        return ""
    texto = texto.strip()
    # RFC 2822 (RSS pubDate)
    try:
        dt = parsedate_to_datetime(texto)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass
    # ISO 8601 (Atom published/updated)
    try:
        # Formatos comunes de Atom
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(texto, fmt)
                return dt.isoformat()
            except ValueError:
                continue
    except (ValueError, TypeError):
        pass
    return texto


def _texto_elemento(elem: ET.Element | None) -> str:
    """Extrae texto de un elemento XML de forma segura."""
    if elem is None:
        return ""
    return (elem.text or "").strip()


def parsear_rss(xml_texto: str, nombre_fuente: str) -> list[dict]:
    """Parsea un feed RSS 2.0 y extrae articulos.

    Args:
        xml_texto: Contenido XML del feed.
        nombre_fuente: Nombre de la fuente para etiquetar articulos.

    Returns:
        Lista de diccionarios con titulo, url, fuente, fecha, contenido.
    """
    articulos = []
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        logger.error(f"Error parseando XML de {nombre_fuente}: {e}")
        return []

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        titulo = _texto_elemento(item.find("title"))
        enlace = _texto_elemento(item.find("link"))
        descripcion = _texto_elemento(item.find("description"))
        fecha = _texto_elemento(item.find("pubDate"))
        contenido = _texto_elemento(item.find("{http://purl.org/rss/1.0/modules/content/}encoded"))

        articulos.append({
            "titulo": titulo,
            "url": enlace,
            "fuente": nombre_fuente,
            "fecha": _parsear_fecha(fecha),
            "contenido": contenido or descripcion,
            "resumen": "",
        })

    return articulos


def parsear_atom(xml_texto: str, nombre_fuente: str) -> list[dict]:
    """Parsea un feed Atom y extrae articulos.

    Args:
        xml_texto: Contenido XML del feed.
        nombre_fuente: Nombre de la fuente para etiquetar articulos.

    Returns:
        Lista de diccionarios con titulo, url, fuente, fecha, contenido.
    """
    articulos = []
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError as e:
        logger.error(f"Error parseando Atom XML de {nombre_fuente}: {e}")
        return []

    for entry in root.iter(f"{_NS_ATOM}entry"):
        titulo = _texto_elemento(entry.find(f"{_NS_ATOM}title"))

        # Atom link: <link href="..." rel="alternate">
        enlace = ""
        for link_elem in entry.findall(f"{_NS_ATOM}link"):
            rel = link_elem.get("rel", "alternate")
            if rel == "alternate":
                enlace = link_elem.get("href", "")
                break
        if not enlace:
            # Primer link sin rel
            primer_link = entry.find(f"{_NS_ATOM}link")
            if primer_link is not None:
                enlace = primer_link.get("href", "")

        resumen = _texto_elemento(entry.find(f"{_NS_ATOM}summary"))
        contenido_elem = entry.find(f"{_NS_ATOM}content")
        contenido = _texto_elemento(contenido_elem) if contenido_elem is not None else ""
        fecha = _texto_elemento(entry.find(f"{_NS_ATOM}published"))
        if not fecha:
            fecha = _texto_elemento(entry.find(f"{_NS_ATOM}updated"))

        articulos.append({
            "titulo": titulo,
            "url": enlace,
            "fuente": nombre_fuente,
            "fecha": _parsear_fecha(fecha),
            "contenido": contenido or resumen,
            "resumen": "",
        })

    return articulos


def parsear_feed(xml_texto: str, nombre_fuente: str) -> list[dict]:
    """Detecta si el feed es RSS o Atom y parsea en consecuencia.

    Args:
        xml_texto: Contenido XML del feed.
        nombre_fuente: Nombre de la fuente.

    Returns:
        Lista de articulos parseados.
    """
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError:
        return []

    # Atom: elemento raiz tiene namespace Atom
    if root.tag == f"{_NS_ATOM}feed":
        return parsear_atom(xml_texto, nombre_fuente)

    # RSS 2.0: elemento raiz es <rss>
    if root.tag == "rss" or root.find("channel") is not None:
        return parsear_rss(xml_texto, nombre_fuente)

    # Intento generico: buscar <item> o <entry>
    if list(root.iter("item")):
        return parsear_rss(xml_texto, nombre_fuente)
    if list(root.iter(f"{_NS_ATOM}entry")):
        return parsear_atom(xml_texto, nombre_fuente)

    logger.warning(f"Formato desconocido para {nombre_fuente}")
    return []


# -- Descarga de feeds -------------------------------------------------


def descargar_feed(url: str, timeout: int = 15) -> str | None:
    """Descarga el contenido de un feed RSS/Atom.

    Args:
        url: URL del feed.
        timeout: Timeout en segundos.

    Returns:
        Contenido XML como string, o None si falla.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ExploraFeeds/1.0 (Linux; Automatizaciones)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.error(f"Error descargando {url}: {e}")
        return None


# -- Cache de articulos ------------------------------------------------


def _ruta_cache(config: dict) -> Path:
    """Obtiene la ruta del fichero de cache desde la config."""
    return Path(config.get("cache", {}).get("ruta", "/tmp/explorador_feeds_cache.json"))


def cargar_cache(config: dict) -> list[dict]:
    """Carga los articulos de la cache.

    Args:
        config: Configuracion de la automatizacion.

    Returns:
        Lista de articulos cacheados.
    """
    ruta = _ruta_cache(config)
    if not ruta.exists():
        return []
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Error leyendo cache: {e}")
        return []


def guardar_cache(articulos: list[dict], config: dict) -> None:
    """Guarda los articulos en la cache JSON.

    Args:
        articulos: Lista de articulos.
        config: Configuracion de la automatizacion.
    """
    ruta = _ruta_cache(config)
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(articulos, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.error(f"Error guardando cache: {e}")


# -- Resumen con LLM --------------------------------------------------


def resumir_articulo(contenido: str) -> str | None:
    """Resume un articulo usando LLM local si esta disponible.

    Args:
        contenido: Texto del articulo (se trunca a 2000 caracteres).

    Returns:
        Resumen en 2 frases o None si no hay LLM.
    """
    if not llm_disponible():
        return None

    return consultar_llm(
        f"Resume este articulo en 2 frases cortas en espanol:\n\n{contenido[:2000]}",
        sistema="Eres un asistente que resume noticias. Responde solo con el resumen, sin preambulos.",
        timeout=15,
    )


# -- Actualizacion de feeds --------------------------------------------


def actualizar_feeds(config: dict) -> list[dict]:
    """Descarga articulos nuevos de todas las fuentes.

    Args:
        config: Configuracion de la automatizacion.

    Returns:
        Lista de articulos nuevos descargados.
    """
    fuentes = cargar_fuentes()
    if not fuentes:
        logger.info("No hay fuentes configuradas")
        return []

    cache_existente = cargar_cache(config)
    urls_existentes = {a["url"] for a in cache_existente if a.get("url")}
    max_por_fuente = config.get("cache", {}).get("max_por_fuente", 20)
    usar_llm = config.get("llm", {}).get("resumir", True)

    todos_nuevos = []

    for fuente in fuentes:
        nombre = fuente["nombre"]
        url = fuente["url"]
        logger.info(f"Descargando feed: {nombre} ({url})")

        xml = descargar_feed(url)
        if xml is None:
            continue

        articulos = parsear_feed(xml, nombre)
        nuevos = [a for a in articulos if a["url"] and a["url"] not in urls_existentes]
        nuevos = nuevos[:max_por_fuente]

        # Resumir con LLM si esta habilitado y disponible
        if usar_llm and nuevos:
            for art in nuevos:
                if art["contenido"]:
                    resumen = resumir_articulo(art["contenido"])
                    if resumen:
                        art["resumen"] = resumen

        todos_nuevos.extend(nuevos)
        logger.info(f"  {nombre}: {len(nuevos)} articulos nuevos")

    # Combinar con cache existente (nuevos al inicio)
    combinados = todos_nuevos + cache_existente
    guardar_cache(combinados, config)

    return todos_nuevos


# -- Formato de tiempo relativo ----------------------------------------


def _hace_tiempo(fecha_iso: str) -> str:
    """Convierte una fecha ISO a formato relativo ('hace 3h').

    Args:
        fecha_iso: Fecha en formato ISO 8601.

    Returns:
        Texto relativo o la fecha original si no se puede calcular.
    """
    if not fecha_iso:
        return "sin fecha"
    try:
        # Parsear fecha ISO
        texto = fecha_iso.replace("Z", "+00:00")
        if "+" not in texto and texto.count("-") <= 2:
            texto += "+00:00"
        dt = datetime.fromisoformat(texto)
        ahora = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = ahora - dt
        segundos = int(delta.total_seconds())

        if segundos < 0:
            return "futuro"
        if segundos < 60:
            return "hace un momento"
        if segundos < 3600:
            mins = segundos // 60
            return f"hace {mins}min"
        if segundos < 86400:
            horas = segundos // 3600
            return f"hace {horas}h"
        dias = segundos // 86400
        if dias == 1:
            return "ayer"
        if dias < 30:
            return f"hace {dias}d"
        return fecha_iso[:10]
    except (ValueError, TypeError):
        return fecha_iso[:16] if len(fecha_iso) > 16 else fecha_iso


# -- Salida formateada -------------------------------------------------


def mostrar_articulos(articulos: list[dict], limite: int = 0) -> None:
    """Muestra la lista de articulos con formato visual.

    Args:
        articulos: Lista de articulos a mostrar.
        limite: Maximo de articulos (0 = todos).
    """
    if not articulos:
        print(f"\n  {_color('No hay articulos en la cache.', DIM)}")
        print(f"  Ejecuta: {_color('feeds --actualizar', CYAN)}\n")
        return

    # Filtrar articulos de hoy
    hoy = datetime.now(timezone.utc).date()
    de_hoy = []
    for art in articulos:
        if art.get("fecha"):
            try:
                texto = art["fecha"].replace("Z", "+00:00")
                if "+" not in texto and texto.count("-") <= 2:
                    texto += "+00:00"
                dt = datetime.fromisoformat(texto)
                if dt.date() == hoy:
                    de_hoy.append(art)
                    continue
            except (ValueError, TypeError):
                pass
        de_hoy.append(art)

    mostrar = de_hoy if de_hoy else articulos
    if limite > 0:
        mostrar = mostrar[:limite]

    total = len(mostrar)
    fuentes_set = {a["fuente"] for a in mostrar}

    print()
    print(f"  {_color(f'Feeds — {total} articulos', BOLD + CYAN)}")
    print(f"  {_color('=' * 45, DIM)}")
    print()

    for i, art in enumerate(mostrar, 1):
        tiempo = _hace_tiempo(art.get("fecha", ""))
        fuente = art.get("fuente", "Desconocida")
        titulo = art.get("titulo", "Sin titulo")
        resumen = art.get("resumen", "")
        contenido = art.get("contenido", "")

        print(f"  {_color(f'[{i}]', BOLD + YELLOW)} {_color(fuente, MAGENTA)} {_color(f'— {tiempo}', DIM)}")
        print(f"      {titulo}")
        if resumen:
            print(f"      {_color('Resumen:', GREEN)} {resumen[:120]}")
        elif contenido:
            # Mostrar primeros 100 caracteres del contenido limpio
            limpio = contenido.replace("\n", " ").strip()[:100]
            if limpio:
                print(f"      {_color(limpio, DIM)}")
        print()

    print(f"  {_color(f'{len(fuentes_set)} fuentes', DIM)} | "
          f"Para leer completo: {_color('feeds --leer <n>', CYAN)}")
    print()


def mostrar_articulo_completo(articulos: list[dict], numero: int) -> None:
    """Muestra un articulo completo por su numero.

    Args:
        articulos: Lista de articulos.
        numero: Numero del articulo (1-based).
    """
    if not articulos:
        print(f"\n  {_color('No hay articulos en la cache.', DIM)}\n")
        return

    if numero < 1 or numero > len(articulos):
        print(f"\n  {_color(f'Articulo #{numero} no existe.', RED)} "
              f"Hay {len(articulos)} articulos.\n", file=sys.stderr)
        sys.exit(1)

    art = articulos[numero - 1]
    tiempo = _hace_tiempo(art.get("fecha", ""))

    print()
    print(f"  {_color(art.get('fuente', ''), MAGENTA)} {_color(f'— {tiempo}', DIM)}")
    print(f"  {_color(art.get('titulo', 'Sin titulo'), BOLD + CYAN)}")
    print(f"  {_color('=' * 45, DIM)}")

    if art.get("url"):
        print(f"  {_color('URL:', DIM)} {art['url']}")

    print()

    if art.get("resumen"):
        print(f"  {_color('Resumen LLM:', GREEN + BOLD)}")
        print(f"  {art['resumen']}")
        print()

    if art.get("contenido"):
        # Limpiar HTML basico
        contenido = art["contenido"]
        for tag in ["<p>", "</p>", "<br>", "<br/>", "<br />"]:
            contenido = contenido.replace(tag, "\n")
        # Quitar etiquetas HTML restantes
        import re
        contenido = re.sub(r"<[^>]+>", "", contenido)
        contenido = contenido.strip()
        if contenido:
            print(f"  {_color('Contenido:', BOLD)}")
            for linea in contenido.split("\n"):
                linea = linea.strip()
                if linea:
                    print(f"  {linea}")
            print()
    elif not art.get("resumen"):
        print(f"  {_color('No hay contenido disponible para este articulo.', DIM)}")
        print()


def mostrar_fuentes() -> None:
    """Muestra las fuentes RSS configuradas."""
    fuentes = cargar_fuentes()
    if not fuentes:
        print(f"\n  {_color('No hay fuentes configuradas.', DIM)}")
        print(f"  Anade una con: {_color('feeds --anadir <url>', CYAN)}\n")
        return

    print()
    print(f"  {_color('Fuentes RSS configuradas', BOLD + CYAN)}")
    print(f"  {_color('=' * 45, DIM)}")
    print()

    for i, fuente in enumerate(fuentes, 1):
        nombre = fuente.get("nombre", "Sin nombre")
        url = fuente.get("url", "")
        cat = fuente.get("categoria", "general")
        print(f"  {_color(f'[{i}]', BOLD + YELLOW)} {_color(nombre, BOLD)} "
              f"{_color(f'({cat})', DIM)}")
        print(f"      {_color(url, DIM)}")
        print()


# -- CLI ----------------------------------------------------------------


def construir_parser() -> argparse.ArgumentParser:
    """Construye el parser de argumentos."""
    parser = argparse.ArgumentParser(
        prog="feeds",
        description="Lector de RSS — articulos de blogs y noticias.",
        epilog=(
            "Ejemplos:\n"
            "  feeds                           Muestra articulos nuevos\n"
            "  feeds --leer 3                  Lee el articulo #3 completo\n"
            "  feeds --fuentes                 Lista fuentes configuradas\n"
            '  feeds --anadir https://...      Anade una fuente RSS\n'
            '  feeds --borrar "Hacker News"    Elimina una fuente\n'
            "  feeds --actualizar              Descarga articulos ahora\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--leer",
        type=int,
        metavar="N",
        help="Muestra el articulo numero N completo",
    )
    parser.add_argument(
        "--fuentes",
        action="store_true",
        help="Lista las fuentes RSS configuradas",
    )
    parser.add_argument(
        "--anadir", "--añadir",
        metavar="URL",
        help="Anade una nueva fuente RSS",
    )
    parser.add_argument(
        "--borrar",
        metavar="NOMBRE",
        help="Elimina una fuente por nombre",
    )
    parser.add_argument(
        "--actualizar",
        action="store_true",
        help="Descarga articulos nuevos ahora",
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

    config = cargar_config(RUTA_AUTO)
    cfg_notif = config.get("notificacion", {})

    # -- Listar fuentes ------------------------------------------------
    if args.fuentes:
        mostrar_fuentes()
        return

    # -- Anadir fuente -------------------------------------------------
    if args.anadir:
        nombre = añadir_fuente(args.anadir)
        print(f"\n  {_color('Fuente anadida:', GREEN + BOLD)} {nombre}")
        print(f"  URL: {args.anadir}")
        print(f"  Ejecuta {_color('feeds --actualizar', CYAN)} para descargar articulos.\n")
        logger.info(f"Fuente anadida: {nombre} ({args.anadir})")
        return

    # -- Borrar fuente -------------------------------------------------
    # Distinguir "flag no pasado" (None) de "flag pasado con cadena vacía" ("").
    # if args.borrar: es falsy para "", lo que provoca que caiga al bloque por
    # defecto en vez de dar error. Usamos is not None para detectar el flag.
    if args.borrar is not None:
        if borrar_fuente(args.borrar):
            print(f"\n  {_color('Fuente eliminada:', YELLOW + BOLD)} {args.borrar}\n")
            logger.info(f"Fuente eliminada: {args.borrar}")
        else:
            print(f"\n  {_color('No se encontro:', RED)} {args.borrar}\n")
        return

    # -- Actualizar feeds ----------------------------------------------
    if args.actualizar:
        print(f"\n  {_color('Actualizando feeds...', CYAN + BOLD)}\n")
        nuevos = actualizar_feeds(config)
        total = len(nuevos)
        fuentes_set = {a["fuente"] for a in nuevos}

        if total > 0:
            print(f"  {_color(f'{total} articulos nuevos', GREEN + BOLD)} "
                  f"de {len(fuentes_set)} fuentes\n")
            if not args.silent:
                notificar(
                    "explorador_feeds",
                    f"{total} articulos nuevos en {len(fuentes_set)} fuentes",
                    cfg_notif.get("severidad", "info"),
                    cfg_notif.get("duracion", 5000),
                )
        else:
            print(f"  {_color('No hay articulos nuevos.', DIM)}\n")
        return

    # -- Leer articulo completo ----------------------------------------
    articulos = cargar_cache(config)
    if args.leer is not None:
        mostrar_articulo_completo(articulos, args.leer)
        return

    # -- Mostrar articulos (por defecto) -------------------------------
    mostrar_articulos(articulos)


if __name__ == "__main__":
    main()
