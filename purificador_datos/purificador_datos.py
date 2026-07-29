#!/usr/bin/env python3
"""
Purificador de Datos — Detector de Archivos Duplicados
Escanea carpetas buscando archivos duplicados por contenido real (hash SHA256).
Genera informe con los duplicados encontrados y espacio recuperable.
NUNCA borra automaticamente — solo informa. El usuario decide.
Parte del ecosistema: mantenimiento.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("purificador_datos")

CONSEJERO = "purificador_datos"

# Tamaño del bloque para hash parcial (primeros 4KB)
TAMANO_HASH_PARCIAL = 4096

# Umbral para mostrar progreso en terminal (100MB)
UMBRAL_PROGRESO = 100 * 1024 * 1024

# -- Colores ANSI --------------------------------------------------------------

_C = {
    "cyan": "\033[36m",
    "amarillo": "\033[33m",
    "verde": "\033[32m",
    "rojo": "\033[31m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


# -- Formato de tamaños --------------------------------------------------------

def _formato_tamano(n_bytes: int) -> str:
    """Convierte bytes a formato legible: 145 B, 3.2 KB, 1.5 MB, 4.2 GB."""
    if n_bytes < 1024:
        return f"{n_bytes} B"
    elif n_bytes < 1024 ** 2:
        return f"{n_bytes / 1024:.1f} KB"
    elif n_bytes < 1024 ** 3:
        return f"{n_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{n_bytes / (1024 ** 3):.1f} GB"


# -- Parseo de tamaño mínimo desde CLI -----------------------------------------

def _parsear_tamano_minimo(texto: str) -> int:
    """Parsea texto como '1M', '500K', '2G' a bytes."""
    texto = texto.strip().upper()
    multiplicadores = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}

    if not texto:
        return 0

    # Si termina en una letra de unidad
    if texto[-1] in multiplicadores:
        try:
            valor = float(texto[:-1])
            return int(valor * multiplicadores[texto[-1]])
        except ValueError:
            pass

    # Si es solo un numero
    try:
        return int(texto)
    except ValueError:
        logger.warning(f"No se pudo parsear tamano minimo: {texto}")
        return 0


# -- Recoleccion de archivos ---------------------------------------------------

def recolectar_archivos(
    carpetas: list[Path],
    min_bytes: int = 0,
    ignorar_ext: set[str] | None = None,
    ignorar_dirs: set[str] | None = None,
    max_archivos: int = 100000,
) -> list[Path]:
    """
    Recorre las carpetas y devuelve archivos candidatos a duplicados.

    No sigue symlinks. Excluye extensiones y carpetas configuradas.
    Respeta el limite de max_archivos como seguridad.

    Args:
        carpetas: Lista de carpetas a escanear.
        min_bytes: Tamano minimo de archivo en bytes.
        ignorar_ext: Extensiones a ignorar (con punto, ej: {".tmp"}).
        ignorar_dirs: Nombres de carpeta a excluir (ej: {".git"}).
        max_archivos: Limite de seguridad.

    Returns:
        Lista de rutas de archivos candidatos.
    """
    if ignorar_ext is None:
        ignorar_ext = set()
    if ignorar_dirs is None:
        ignorar_dirs = set()

    archivos: list[Path] = []

    for carpeta in carpetas:
        if not carpeta.is_dir():
            logger.warning(f"Carpeta no encontrada: {carpeta}")
            continue

        for raiz, dirs, ficheros in os.walk(carpeta, followlinks=False):
            # Excluir carpetas ignoradas (modificar dirs in-place)
            dirs[:] = [d for d in dirs if d not in ignorar_dirs]

            for nombre in ficheros:
                if len(archivos) >= max_archivos:
                    logger.warning(
                        f"Limite de {max_archivos} archivos alcanzado, "
                        f"deteniendo escaneo"
                    )
                    return archivos

                ruta = Path(raiz) / nombre

                # No seguir symlinks
                if ruta.is_symlink():
                    continue

                # Comprobar extension
                if ruta.suffix.lower() in ignorar_ext:
                    continue

                # Comprobar tamano
                try:
                    tamano = ruta.stat().st_size
                except OSError:
                    continue

                if tamano < min_bytes:
                    continue

                # Archivos de tamano 0 no pueden ser duplicados utiles
                if tamano == 0:
                    continue

                archivos.append(ruta)

    return archivos


# -- Agrupacion por tamano -----------------------------------------------------

def agrupar_por_tamano(archivos: list[Path]) -> dict[int, list[Path]]:
    """
    Agrupa archivos por tamano. Solo devuelve grupos con 2+ archivos
    (posibles duplicados).
    """
    por_tamano: dict[int, list[Path]] = defaultdict(list)

    for ruta in archivos:
        try:
            tamano = ruta.stat().st_size
            por_tamano[tamano].append(ruta)
        except OSError:
            continue

    # Filtrar: solo grupos con mas de un archivo
    return {t: rutas for t, rutas in por_tamano.items() if len(rutas) > 1}


# -- Hash parcial (primeros 4KB) -----------------------------------------------

def _hash_parcial(ruta: Path) -> str | None:
    """Calcula hash SHA256 de los primeros 4KB del archivo."""
    try:
        with open(ruta, "rb") as f:
            datos = f.read(TAMANO_HASH_PARCIAL)
        return hashlib.sha256(datos).hexdigest()
    except OSError:
        return None


def agrupar_por_hash_parcial(grupo_tamano: list[Path]) -> dict[str, list[Path]]:
    """
    Dentro de un grupo de mismo tamano, agrupa por hash parcial.
    Solo devuelve grupos con 2+ archivos.
    """
    por_hash: dict[str, list[Path]] = defaultdict(list)

    for ruta in grupo_tamano:
        h = _hash_parcial(ruta)
        if h is not None:
            por_hash[h].append(ruta)

    return {h: rutas for h, rutas in por_hash.items() if len(rutas) > 1}


# -- Hash completo SHA256 ------------------------------------------------------

def _hash_completo(ruta: Path, mostrar_progreso: bool = False) -> str | None:
    """
    Calcula hash SHA256 completo del archivo.

    Para archivos grandes (>100MB) y si mostrar_progreso es True,
    imprime progreso en la terminal.
    """
    try:
        tamano = ruta.stat().st_size
        h = hashlib.sha256()
        leidos = 0
        bloque = 8192

        with open(ruta, "rb") as f:
            while True:
                datos = f.read(bloque)
                if not datos:
                    break
                h.update(datos)
                leidos += len(datos)

                if (
                    mostrar_progreso
                    and tamano > UMBRAL_PROGRESO
                    and tamano > 0
                ):
                    pct = (leidos / tamano) * 100
                    print(
                        f"\r  {_C['dim']}Calculando hash: "
                        f"{pct:.0f}% de {_formato_tamano(tamano)}"
                        f"{_C['reset']}",
                        end="",
                        flush=True,
                    )

        if mostrar_progreso and tamano > UMBRAL_PROGRESO:
            print("\r" + " " * 60 + "\r", end="", flush=True)

        return h.hexdigest()
    except OSError:
        return None


def agrupar_por_hash_completo(
    grupo_parcial: list[Path],
    es_terminal: bool = False,
) -> dict[str, list[Path]]:
    """
    Dentro de un grupo de mismo hash parcial, agrupa por hash completo.
    Solo devuelve grupos con 2+ archivos (duplicados confirmados).
    """
    por_hash: dict[str, list[Path]] = defaultdict(list)

    for ruta in grupo_parcial:
        h = _hash_completo(ruta, mostrar_progreso=es_terminal)
        if h is not None:
            por_hash[h].append(ruta)

    return {h: rutas for h, rutas in por_hash.items() if len(rutas) > 1}


# -- Pipeline completo de deteccion --------------------------------------------

def detectar_duplicados(
    carpetas: list[Path],
    min_bytes: int = 0,
    ignorar_ext: set[str] | None = None,
    ignorar_dirs: set[str] | None = None,
    max_archivos: int = 100000,
    es_terminal: bool = False,
) -> list[list[Path]]:
    """
    Pipeline completo de deteccion de duplicados.

    1. Recolecta archivos
    2. Agrupa por tamano
    3. Filtra por hash parcial (4KB)
    4. Confirma con hash completo SHA256

    Args:
        carpetas: Carpetas a escanear.
        min_bytes: Tamano minimo.
        ignorar_ext: Extensiones a ignorar.
        ignorar_dirs: Carpetas a excluir.
        max_archivos: Limite de seguridad.
        es_terminal: Si True, muestra progreso para archivos grandes.

    Returns:
        Lista de grupos de duplicados. Cada grupo es una lista de rutas.
    """
    if es_terminal:
        print(f"\n{_C['dim']}  Recolectando archivos...{_C['reset']}", flush=True)

    archivos = recolectar_archivos(
        carpetas, min_bytes, ignorar_ext, ignorar_dirs, max_archivos
    )
    total_archivos = len(archivos)

    if es_terminal:
        print(
            f"  {_C['dim']}{total_archivos} archivos encontrados{_C['reset']}",
            flush=True,
        )

    logger.info(f"Archivos recolectados: {total_archivos}")

    if total_archivos < 2:
        return []

    # Paso 1: Agrupar por tamano
    if es_terminal:
        print(
            f"  {_C['dim']}Agrupando por tamano...{_C['reset']}",
            flush=True,
        )

    grupos_tamano = agrupar_por_tamano(archivos)
    candidatos_tamano = sum(len(v) for v in grupos_tamano.values())
    logger.info(
        f"Candidatos por tamano: {candidatos_tamano} archivos "
        f"en {len(grupos_tamano)} grupos"
    )

    if not grupos_tamano:
        return []

    # Paso 2: Hash parcial
    if es_terminal:
        print(
            f"  {_C['dim']}Calculando hash parcial ({candidatos_tamano} "
            f"candidatos)...{_C['reset']}",
            flush=True,
        )

    candidatos_parcial: list[list[Path]] = []
    for rutas in grupos_tamano.values():
        grupos_parcial = agrupar_por_hash_parcial(rutas)
        candidatos_parcial.extend(grupos_parcial.values())

    total_parcial = sum(len(g) for g in candidatos_parcial)
    logger.info(
        f"Candidatos por hash parcial: {total_parcial} archivos "
        f"en {len(candidatos_parcial)} grupos"
    )

    if not candidatos_parcial:
        return []

    # Paso 3: Hash completo
    if es_terminal:
        print(
            f"  {_C['dim']}Verificando con hash completo ({total_parcial} "
            f"candidatos)...{_C['reset']}",
            flush=True,
        )

    duplicados: list[list[Path]] = []
    for grupo in candidatos_parcial:
        grupos_completo = agrupar_por_hash_completo(grupo, es_terminal)
        duplicados.extend(grupos_completo.values())

    total_dupes = sum(len(g) for g in duplicados)
    logger.info(f"Duplicados confirmados: {total_dupes} archivos en {len(duplicados)} grupos")

    return duplicados


# -- Calculo de espacio recuperable ---------------------------------------------

def calcular_espacio_recuperable(duplicados: list[list[Path]]) -> int:
    """
    Calcula el espacio total que se podria recuperar eliminando duplicados.
    Por cada grupo, se conserva una copia y el resto es espacio recuperable.
    """
    total = 0
    for grupo in duplicados:
        if len(grupo) < 2:
            continue
        try:
            tamano = grupo[0].stat().st_size
            # Se conserva 1 copia, el resto es recuperable
            total += tamano * (len(grupo) - 1)
        except OSError:
            continue
    return total


# -- Salida en terminal ---------------------------------------------------------

def _ruta_corta(ruta: Path) -> str:
    """Convierte ruta absoluta a formato con ~/ si es posible."""
    try:
        return f"~/{ruta.relative_to(Path.home())}"
    except ValueError:
        return str(ruta)


def _abrir_carpeta(ruta: Path):
    """Abre la carpeta contenedora del archivo en el explorador."""
    carpeta = ruta.parent if ruta.is_file() else ruta
    subprocess.Popen(
        ["xdg-open", str(carpeta)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def mostrar_duplicados(duplicados: list[list[Path]], interactivo: bool = True):
    """Muestra los duplicados encontrados en la terminal con colores."""
    c = _C
    total_archivos = sum(len(g) for g in duplicados)
    espacio = calcular_espacio_recuperable(duplicados)

    print()
    print(
        f"  {c['bold']}Duplicados encontrados: "
        f"{c['cyan']}{len(duplicados)} grupos{c['reset']}{c['bold']}, "
        f"{total_archivos} archivos{c['reset']}"
    )
    print(
        f"  {c['bold']}Espacio recuperable: "
        f"{c['amarillo']}{_formato_tamano(espacio)}{c['reset']}"
    )
    print()

    for i, grupo in enumerate(duplicados, 1):
        try:
            tamano = grupo[0].stat().st_size
        except OSError:
            tamano = 0

        copias = len(grupo)
        print(
            f"  {c['cyan']}[{i}]{c['reset']} "
            f"{copias} copias — "
            f"{c['amarillo']}{_formato_tamano(tamano)}{c['reset']}"
        )
        for j, ruta in enumerate(grupo, 1):
            print(f"    {c['dim']}{j}.{c['reset']} {_ruta_corta(ruta)}")

        if interactivo and sys.stdout.isatty():
            try:
                resp = input(
                    f"    {c['dim']}Abrir carpeta? (y/n): {c['reset']}"
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                continue

            if resp == "y":
                _abrir_carpeta(grupo[0])
                print(f"    {c['verde']}Abierta: {grupo[0].parent}{c['reset']}")
        print()


def mostrar_sin_duplicados():
    """Muestra mensaje cuando no se encuentran duplicados."""
    c = _C
    print()
    print(f"  {c['verde']}Sin duplicados encontrados.{c['reset']}")
    print()


# -- Modo interactivo --borrar --------------------------------------------------

def _ruta_papelera() -> Path:
    """Obtiene la ruta de la papelera del sistema o una alternativa."""
    # Papelera freedesktop estandar
    trash = Path.home() / ".local" / "share" / "Trash" / "files"
    if trash.parent.exists():
        trash.mkdir(parents=True, exist_ok=True)
        return trash

    # Alternativa si no existe estructura Trash
    alternativa = Path.home() / ".papelera_dupes"
    alternativa.mkdir(parents=True, exist_ok=True)
    return alternativa


def _mover_a_papelera(ruta: Path, papelera: Path) -> bool:
    """Mueve un archivo a la papelera. Devuelve True si tuvo exito."""
    try:
        destino = papelera / ruta.name
        # Si ya existe un archivo con ese nombre, anadir timestamp
        if destino.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            destino = papelera / f"{ruta.stem}_{ts}{ruta.suffix}"

        shutil.move(str(ruta), str(destino))
        logger.info(f"Movido a papelera: {ruta} -> {destino}")
        return True
    except OSError as e:
        logger.error(f"Error moviendo {ruta} a papelera: {e}")
        return False


def modo_borrar(duplicados: list[list[Path]]):
    """
    Modo interactivo: para cada grupo muestra las copias numeradas
    y pregunta cual borrar. Mueve a papelera, NUNCA borra definitivamente.
    """
    c = _C
    papelera = _ruta_papelera()
    total_borrados = 0
    total_espacio = 0

    print()
    print(
        f"  {c['bold']}Modo interactivo — los archivos se moveran a la "
        f"papelera{c['reset']}"
    )
    print(f"  {c['dim']}Papelera: {papelera}{c['reset']}")
    print()

    for i, grupo in enumerate(duplicados, 1):
        try:
            tamano = grupo[0].stat().st_size
        except OSError:
            tamano = 0

        print(
            f"  {c['cyan']}--- Grupo {i}/{len(duplicados)} --- "
            f"{c['amarillo']}{_formato_tamano(tamano)} c/u{c['reset']}"
        )

        for j, ruta in enumerate(grupo, 1):
            print(f"  {c['cyan']}[{j}]{c['reset']} {_ruta_corta(ruta)}")

        print()
        try:
            respuesta = input(
                f"  {c['bold']}Cuales borrar? "
                f"{c['dim']}(ej: 2,3 o 'n' para saltar): {c['reset']}"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {c['dim']}Cancelado.{c['reset']}\n")
            break

        if not respuesta or respuesta.lower() == "n":
            print(f"  {c['dim']}Saltado.{c['reset']}")
            print()
            continue

        # Parsear numeros seleccionados
        try:
            indices = [int(x.strip()) for x in respuesta.split(",")]
        except ValueError:
            print(f"  {c['rojo']}Entrada no valida, saltando grupo.{c['reset']}")
            print()
            continue

        for idx in indices:
            if idx < 1 or idx > len(grupo):
                print(
                    f"  {c['rojo']}Indice {idx} fuera de rango, "
                    f"saltando.{c['reset']}"
                )
                continue

            ruta = grupo[idx - 1]
            if _mover_a_papelera(ruta, papelera):
                total_borrados += 1
                total_espacio += tamano
                print(
                    f"  {c['verde']}Movido: {_ruta_corta(ruta)}{c['reset']}"
                )

        print()

    print(
        f"  {c['bold']}Resultado: {total_borrados} archivos movidos a "
        f"papelera, {_formato_tamano(total_espacio)} liberados{c['reset']}"
    )
    print()
    logger.info(
        f"Modo borrar: {total_borrados} archivos movidos, "
        f"{_formato_tamano(total_espacio)} liberados"
    )


# -- Informe en archivo ---------------------------------------------------------

def generar_informe(duplicados: list[list[Path]], carpetas: list[Path]) -> str:
    """Genera informe de duplicados en texto plano."""
    total_archivos = sum(len(g) for g in duplicados)
    espacio = calcular_espacio_recuperable(duplicados)
    ahora = datetime.now()

    lineas: list[str] = []
    sep = "=" * 55
    sep_sub = "-" * 55

    lineas.append(f"  {sep}")
    lineas.append(f"  INFORME DE DUPLICADOS — {ahora:%d %b %Y %H:%M}")
    lineas.append(f"  {sep}")
    lineas.append("")

    lineas.append(f"  Carpetas escaneadas:")
    for carpeta in carpetas:
        lineas.append(f"    {_ruta_corta(carpeta)}")
    lineas.append("")

    lineas.append(f"  RESUMEN")
    lineas.append(f"  {sep_sub}")
    lineas.append(f"  Grupos de duplicados: {len(duplicados)}")
    lineas.append(f"  Total archivos duplicados: {total_archivos}")
    lineas.append(f"  Espacio recuperable: {_formato_tamano(espacio)}")
    lineas.append("")

    if duplicados:
        lineas.append(f"  DETALLE")
        lineas.append(f"  {sep_sub}")

        for i, grupo in enumerate(duplicados, 1):
            try:
                tamano = grupo[0].stat().st_size
            except OSError:
                tamano = 0

            lineas.append(
                f"  [{i}] {len(grupo)} copias — {_formato_tamano(tamano)}"
            )
            for ruta in grupo:
                lineas.append(f"      {_ruta_corta(ruta)}")
            lineas.append("")

    lineas.append(f"  {sep}")

    return "\n".join(lineas)


def guardar_informe(texto: str) -> Path:
    """Guarda el informe en el directorio de informes."""
    ruta_dir = (
        Path.home()
        / "Escritorio"
        / "automatizaciones"
        / "logs"
        / "informes"
    )
    ruta_dir.mkdir(parents=True, exist_ok=True)

    nombre = f"duplicados_{datetime.now():%Y-%m-%d}.txt"
    ruta_archivo = ruta_dir / nombre

    try:
        ruta_archivo.write_text(texto, encoding="utf-8")
        logger.info(f"Informe guardado en {ruta_archivo}")
    except OSError as e:
        logger.error(f"No se pudo guardar informe: {e}")

    return ruta_archivo


# -- Notificacion ---------------------------------------------------------------

def enviar_notificacion(duplicados: list[list[Path]], config: dict):
    """Envia notificacion con resumen de duplicados encontrados."""
    cfg_notif = config.get("notificacion", {})
    duracion = cfg_notif.get("duracion", 8000)

    if duplicados:
        total = sum(len(g) for g in duplicados)
        espacio = calcular_espacio_recuperable(duplicados)
        msg = (
            f"{len(duplicados)} duplicados encontrados, "
            f"{_formato_tamano(espacio)} recuperables\n"
            f"Ejecuta 'dupes' para verlos"
        )
        severidad = cfg_notif.get("severidad", "info")
    else:
        msg = "Sin duplicados encontrados"
        severidad = "exito"

    notificar(CONSEJERO, msg, severidad, duracion)
    logger.info(f"Notificacion enviada: {msg} (severidad={severidad})")


# -- Ejecucion principal (modo servicio) ----------------------------------------

def ejecutar(config: dict):
    """Ejecucion completa: escanea, genera informe y notifica."""
    cfg_escaneo = config.get("escaneo", {})

    carpetas = [
        Path(c).expanduser()
        for c in cfg_escaneo.get(
            "carpetas",
            ["~/Descargas", "~/Documentos", "~/Música", "~/Imágenes"],
        )
    ]
    min_bytes = cfg_escaneo.get("min_bytes", 1024)
    ignorar_ext = set(cfg_escaneo.get("ignorar_extensiones", [".tmp", ".swp", ".lock", ".pid"]))
    ignorar_dirs = set(cfg_escaneo.get("ignorar_carpetas", [".git", "node_modules", "__pycache__", ".cache"]))
    max_archivos = cfg_escaneo.get("max_archivos", 100000)

    duplicados = detectar_duplicados(
        carpetas, min_bytes, ignorar_ext, ignorar_dirs, max_archivos
    )

    texto = generar_informe(duplicados, carpetas)
    guardar_informe(texto)
    enviar_notificacion(duplicados, config)

    logger.info("Ejecucion completada")


# -- CLI: comando 'dupes' -------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Purificador de Datos — Detector de archivos duplicados"
    )

    parser.add_argument(
        "carpetas", nargs="*", default=None,
        help="Carpetas a escanear (por defecto: las configuradas)"
    )
    parser.add_argument(
        "--min", dest="min_tamano", type=str, default=None,
        help="Tamano minimo de archivo (ej: 1M, 500K, 2G)"
    )
    parser.add_argument(
        "--informe", action="store_true",
        help="Genera informe detallado en archivo"
    )
    parser.add_argument(
        "--borrar", action="store_true",
        help="Modo interactivo: pregunta que duplicados mover a papelera"
    )

    args = parser.parse_args()
    config = cargar_config(RUTA_AUTO)
    cfg_escaneo = config.get("escaneo", {})

    # Determinar carpetas
    if args.carpetas:
        carpetas = [Path(c).expanduser().resolve() for c in args.carpetas]
        # Validar que todas las rutas explícitas del usuario son directorios reales
        for carpeta in carpetas:
            if not carpeta.is_dir():
                print(
                    f"  Error: '{carpeta}' no es un directorio existente",
                    file=sys.stderr,
                )
                sys.exit(1)
    else:
        carpetas = [
            Path(c).expanduser()
            for c in cfg_escaneo.get(
                "carpetas",
                ["~/Descargas", "~/Documentos", "~/Música", "~/Imágenes"],
            )
        ]

    # Determinar tamano minimo
    if args.min_tamano:
        min_bytes = _parsear_tamano_minimo(args.min_tamano)
    else:
        min_bytes = cfg_escaneo.get("min_bytes", 1024)

    ignorar_ext = set(
        cfg_escaneo.get("ignorar_extensiones", [".tmp", ".swp", ".lock", ".pid"])
    )
    ignorar_dirs = set(
        cfg_escaneo.get("ignorar_carpetas", [".git", "node_modules", "__pycache__", ".cache"])
    )
    max_archivos = cfg_escaneo.get("max_archivos", 100000)

    # Ejecutar deteccion
    es_terminal = sys.stdout.isatty()
    duplicados = detectar_duplicados(
        carpetas, min_bytes, ignorar_ext, ignorar_dirs, max_archivos,
        es_terminal=es_terminal,
    )

    # Mostrar resultado
    if duplicados:
        mostrar_duplicados(duplicados, interactivo=not args.borrar)

        if args.borrar:
            modo_borrar(duplicados)

        if args.informe:
            texto = generar_informe(duplicados, carpetas)
            ruta = guardar_informe(texto)
            c = _C
            print(
                f"  {c['verde']}Informe guardado en: {ruta}{c['reset']}\n"
            )

        enviar_notificacion(duplicados, config)
    else:
        mostrar_sin_duplicados()
        enviar_notificacion(duplicados, config)


if __name__ == "__main__":
    main()
