#!/usr/bin/env python3
"""
Limpiador Semanal
Limpia archivos temporales y basura del sistema.
Soporta --dry-run para ver qué se limpiaría sin borrar nada.
"""

import argparse
import sys
import shutil
import subprocess
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("limpiador")

MODO_SECO = False


def _ejecutar(cmd: list[str], sudo: bool = False) -> subprocess.CompletedProcess:
    if sudo:
        cmd = ["sudo"] + cmd
    if MODO_SECO:
        logger.info(f"[DRY-RUN] Ejecutaría: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _tamano_directorio(ruta: Path) -> int:
    total = 0
    if not ruta.exists():
        return 0
    for f in ruta.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except (PermissionError, FileNotFoundError):
                pass
    return total


def _formato_espacio(bytes_: int) -> str:
    if bytes_ < 1024:
        return f"{bytes_} B"
    elif bytes_ < 1024 ** 2:
        return f"{bytes_ / 1024:.1f} KB"
    elif bytes_ < 1024 ** 3:
        return f"{bytes_ / 1024 ** 2:.1f} MB"
    else:
        return f"{bytes_ / 1024 ** 3:.2f} GB"


def _contar_archivos_antiguos(ruta: Path, limite_tiempo: float) -> tuple[int, int]:
    count = 0
    size = 0
    for f in ruta.rglob("*"):
        if f.is_file():
            try:
                st = f.stat()
                if st.st_mtime < limite_tiempo:
                    count += 1
                    size += st.st_size
            except (PermissionError, FileNotFoundError):
                pass
    return count, size


def _limpiar_archivos_antiguos(ruta: Path, limite_tiempo: float) -> int:
    liberado = 0
    for f in ruta.rglob("*"):
        if f.is_file():
            try:
                if f.stat().st_mtime < limite_tiempo:
                    liberado += f.stat().st_size
                    if not MODO_SECO:
                        f.unlink()
            except (PermissionError, FileNotFoundError):
                pass
    if not MODO_SECO:
        for d in sorted(ruta.rglob("*"), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass
    return liberado


def limpiar_cache_pacman(versiones: int = 3) -> int:
    logger.info("Limpiando caché de pacman...")
    if MODO_SECO:
        antes = _tamano_directorio(Path("/var/cache/pacman/pkg"))
        logger.info(f"[DRY-RUN] Caché pacman actual: {_formato_espacio(antes)} (paccache -rk{versiones})")
        return antes // 4  # estimación conservadora
    antes = _tamano_directorio(Path("/var/cache/pacman/pkg"))
    result = _ejecutar(["paccache", "-rk", str(versiones)], sudo=True)
    if result.returncode != 0:
        logger.error(f"Error limpiando caché pacman: {result.stderr}")
        return 0
    despues = _tamano_directorio(Path("/var/cache/pacman/pkg"))
    liberado = max(0, antes - despues)
    logger.info(f"Caché pacman: liberados {_formato_espacio(liberado)}")
    return liberado


def limpiar_cache_yay(ruta: Path, dias: int = 30) -> int:
    logger.info(f"Limpiando caché de yay (>{dias} días)...")
    if not ruta.exists():
        logger.info("Directorio de caché yay no existe, saltando")
        return 0

    limite = time.time() - (dias * 86400)
    liberado = 0

    for paquete_dir in ruta.iterdir():
        if not paquete_dir.is_dir():
            continue
        for subdir in ("src", "pkg"):
            build_dir = paquete_dir / subdir
            if build_dir.exists():
                count, size = _contar_archivos_antiguos(build_dir, limite)
                if count > 0:
                    logger.info(
                        f"  yay/{paquete_dir.name}/{subdir}: "
                        f"{count} archivos, {_formato_espacio(size)}"
                    )
                    if MODO_SECO:
                        liberado += size
                    else:
                        liberado += size
                        shutil.rmtree(build_dir)

    logger.info(f"Caché yay: liberados {_formato_espacio(liberado)}")
    return liberado


def limpiar_cache_apps(ruta: Path, dias: int = 7, whitelist: list[str] | None = None) -> int:
    logger.info(f"Limpiando caché de aplicaciones (>{dias} días, whitelist)...")
    if not ruta.exists():
        logger.info("Directorio de caché no existe, saltando")
        return 0

    whitelist = set(whitelist or [])
    if not whitelist:
        logger.info("Whitelist vacía, saltando limpieza de caché de apps")
        return 0

    liberado = 0
    limite = time.time() - (dias * 86400)

    for entrada in ruta.iterdir():
        if entrada.name not in whitelist:
            continue
        try:
            if entrada.is_file() and entrada.stat().st_mtime < limite:
                tam = entrada.stat().st_size
                if MODO_SECO:
                    logger.info(f"[DRY-RUN] Borraría archivo: {entrada.name} ({_formato_espacio(tam)})")
                else:
                    entrada.unlink()
                liberado += tam
            elif entrada.is_dir():
                count, size = _contar_archivos_antiguos(entrada, limite)
                if count > 0:
                    logger.info(
                        f"  {entrada.name}: {count} archivos viejos, "
                        f"{_formato_espacio(size)}"
                    )
                    liberado += _limpiar_archivos_antiguos(entrada, limite)
        except (PermissionError, FileNotFoundError) as e:
            logger.warning(f"No se pudo limpiar {entrada.name}: {e}")

    logger.info(f"Caché apps: liberados {_formato_espacio(liberado)}")
    return liberado


def limpiar_logs_sistema(dias: int = 7) -> int:
    logger.info(f"Limpiando logs del sistema (>{dias} días)...")
    result = _ejecutar(["journalctl", "--vacuum-time", f"{dias}d"], sudo=True)
    if result.returncode != 0:
        logger.error(f"Error limpiando logs: {result.stderr}")
        return 0
    for linea in result.stdout.splitlines():
        logger.info(f"journalctl: {linea.strip()}")
    return 0


def limpiar_paquetes_huerfanos() -> int:
    logger.info("Buscando paquetes huérfanos...")
    result = subprocess.run(
        ["pacman", "-Qtdq"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0 or not result.stdout.strip():
        logger.info("No hay paquetes huérfanos")
        return 0

    paquetes = result.stdout.strip().split("\n")
    if MODO_SECO:
        logger.info(f"[DRY-RUN] Eliminaría {len(paquetes)} huérfanos: {', '.join(paquetes)}")
        return 0

    logger.info(f"Encontrados {len(paquetes)} huérfanos: {', '.join(paquetes)}")
    result = _ejecutar(["pacman", "-Rns", "--noconfirm"] + paquetes, sudo=True)
    if result.returncode != 0:
        logger.error(f"Error eliminando huérfanos: {result.stderr}")
        return 0

    logger.info(f"Eliminados {len(paquetes)} paquetes huérfanos")
    return 0


def limpiar_papelera(ruta: Path) -> int:
    logger.info("Vaciando papelera...")
    if not ruta.exists():
        logger.info("Papelera no existe, saltando")
        return 0

    liberado = _tamano_directorio(ruta)

    if MODO_SECO:
        count = sum(1 for _ in ruta.rglob("*") if _.is_file())
        logger.info(f"[DRY-RUN] Vaciaría papelera: {count} archivos, {_formato_espacio(liberado)}")
        return liberado

    for entrada in ruta.iterdir():
        try:
            if entrada.is_dir():
                shutil.rmtree(entrada)
            else:
                entrada.unlink()
        except (PermissionError, FileNotFoundError) as e:
            logger.warning(f"No se pudo eliminar {entrada.name}: {e}")

    logger.info(f"Papelera: liberados {_formato_espacio(liberado)}")
    return liberado


def limpiar_miniaturas(ruta: Path) -> int:
    logger.info("Limpiando miniaturas...")
    if not ruta.exists():
        logger.info("Directorio de miniaturas no existe, saltando")
        return 0

    liberado = _tamano_directorio(ruta)

    if MODO_SECO:
        count = sum(1 for _ in ruta.rglob("*") if _.is_file())
        logger.info(f"[DRY-RUN] Eliminaría {count} miniaturas, {_formato_espacio(liberado)}")
        return liberado

    for f in ruta.rglob("*"):
        if f.is_file():
            try:
                f.unlink()
            except (PermissionError, FileNotFoundError):
                pass

    logger.info(f"Miniaturas: liberados {_formato_espacio(liberado)}")
    return liberado


def main():
    global MODO_SECO

    parser = argparse.ArgumentParser(description="Limpiador semanal del sistema")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simular limpieza sin borrar nada"
    )
    args = parser.parse_args()
    MODO_SECO = args.dry_run

    config = cargar_config(RUTA_AUTO)
    tareas = config.get("tareas", {})
    umbrales = config.get("umbrales", {})
    rutas = config.get("rutas", {})
    whitelist = config.get("whitelist_cache", {}).get("dirs", [])

    modo = "[DRY-RUN] " if MODO_SECO else ""
    notificar("limpiador", f"{modo}Comenzando la limpieza del sistema...", "info")
    logger.info(f"=== {modo}Inicio de limpieza semanal ===")

    resumen = {}
    total_liberado = 0
    errores = []

    try:
        if tareas.get("cache_pacman", True):
            lib = limpiar_cache_pacman(umbrales.get("versiones_pacman", 3))
            resumen["Caché pacman"] = lib
            total_liberado += lib

        if tareas.get("cache_yay", True):
            ruta_yay = Path(rutas.get("cache_yay", "~/.cache/yay")).expanduser()
            lib = limpiar_cache_yay(ruta_yay, umbrales.get("dias_yay", 30))
            resumen["Caché yay"] = lib
            total_liberado += lib

        if tareas.get("cache_apps", True):
            ruta_cache = Path(rutas.get("cache_apps", "~/.cache")).expanduser()
            lib = limpiar_cache_apps(
                ruta_cache, umbrales.get("dias_cache", 7), whitelist
            )
            resumen["Caché apps"] = lib
            total_liberado += lib

        if tareas.get("logs_sistema", True):
            limpiar_logs_sistema(umbrales.get("dias_logs", 7))
            resumen["Logs sistema"] = 0

        if tareas.get("paquetes_huerfanos", True):
            limpiar_paquetes_huerfanos()
            resumen["Huérfanos"] = 0

        if tareas.get("papelera", True):
            ruta_papelera = Path(rutas.get("papelera", "~/.local/share/Trash")).expanduser()
            lib = limpiar_papelera(ruta_papelera)
            resumen["Papelera"] = lib
            total_liberado += lib

        if tareas.get("miniaturas", True):
            ruta_minis = Path(rutas.get("miniaturas", "~/.cache/thumbnails")).expanduser()
            lib = limpiar_miniaturas(ruta_minis)
            resumen["Miniaturas"] = lib
            total_liberado += lib

    except Exception as e:
        logger.critical(f"Error inesperado: {e}")
        errores.append(str(e))

    espacio = _formato_espacio(total_liberado)

    logger.info(f"=== {modo}Resumen ===")
    for tarea, lib in resumen.items():
        logger.info(f"  {tarea}: {_formato_espacio(lib)}")
    logger.info(f"  TOTAL: {espacio}")

    if errores:
        notificar(
            "limpiador",
            f"{modo}Limpieza parcial. Liberados {espacio}. Errores: {'; '.join(errores)}",
            "aviso",
        )
        sys.exit(1)
    else:
        notificar(
            "limpiador",
            f"{modo}Limpieza completada. He liberado {espacio} de basura.",
            "exito",
        )


if __name__ == "__main__":
    main()
