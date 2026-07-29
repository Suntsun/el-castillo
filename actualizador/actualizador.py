#!/usr/bin/env python3
"""
Actualizador Automático
Actualiza paquetes del sistema (pacman + yay/AUR) de forma desatendida.
"""

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_SNAPSHOTS = RUTA_AUTO / "snapshots"
DB_LOCK = Path("/var/lib/pacman/db.lck")
logger = configurar_logger("actualizador")


def pacman_bloqueado() -> bool:
    if DB_LOCK.exists():
        logger.warning(f"Pacman bloqueado ({DB_LOCK} existe). Otro proceso lo está usando.")
        return True
    return False


def guardar_snapshot(pacman: list[str], aur: list[str]):
    RUTA_SNAPSHOTS.mkdir(exist_ok=True)
    fecha = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    fichero = RUTA_SNAPSHOTS / f"{fecha}.txt"

    lineas = []
    if pacman:
        lineas.append("=== PACMAN ===")
        lineas.extend(pacman)
    if aur:
        lineas.append("=== AUR ===")
        lineas.extend(aur)

    fichero.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    logger.info(f"Snapshot pre-actualización guardado: {fichero.name}")


def hay_conexion(host: str = "1.1.1.1", timeout: int = 5) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", str(timeout), host],
        capture_output=True,
    )
    return result.returncode == 0


def listar_actualizaciones_pacman() -> list[str]:
    result = subprocess.run(
        ["checkupdates"], capture_output=True, text=True, timeout=120
    )
    if result.returncode == 2 or not result.stdout.strip():
        return []
    if result.returncode != 0:
        logger.warning(f"checkupdates retornó código {result.returncode}: {result.stderr}")
        return []
    return [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]


def listar_actualizaciones_aur() -> list[str]:
    result = subprocess.run(
        ["yay", "-Qua"], capture_output=True, text=True, timeout=120
    )
    if not result.stdout.strip():
        return []
    return [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]


def actualizar_pacman(dry_run: bool = False) -> tuple[bool, int]:
    if dry_run:
        paquetes = listar_actualizaciones_pacman()
        for p in paquetes:
            logger.info(f"[DRY-RUN] Actualizaría: {p}")
        return True, len(paquetes)

    result = subprocess.run(
        ["sudo", "pacman", "-Syu", "--noconfirm"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        logger.error(f"Error actualizando pacman: {result.stderr}")
        return False, 0

    count = 0
    for linea in result.stdout.splitlines():
        if linea.startswith("upgrading ") or linea.startswith("installing "):
            count += 1
            logger.info(f"  {linea.strip()}")

    return True, count


def actualizar_aur(dry_run: bool = False) -> tuple[bool, int]:
    if dry_run:
        paquetes = listar_actualizaciones_aur()
        for p in paquetes:
            logger.info(f"[DRY-RUN] Actualizaría AUR: {p}")
        return True, len(paquetes)

    result = subprocess.run(
        ["yay", "-Sua", "--noconfirm"],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        logger.error(f"Error actualizando AUR: {result.stderr}")
        return False, 0

    count = 0
    for linea in result.stdout.splitlines():
        if linea.startswith("upgrading ") or linea.startswith("installing "):
            count += 1
            logger.info(f"  AUR: {linea.strip()}")

    return True, count


def main():
    parser = argparse.ArgumentParser(
        description="Actualizador automático del sistema",
        epilog=(
            "Ejemplos:\n"
            "  actualizador --check        Comprueba actualizaciones sin instalar\n"
            "  actualizador --dry-run      Muestra qué se actualizaría\n"
            "  actualizador --apply        Aplica las actualizaciones (acción real)\n"
            "\n"
            "Nota: --check, --dry-run y --apply son mutuamente excluyentes.\n"
            "      Solo se puede usar uno a la vez.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostrar qué se actualizaría sin instalar nada",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Solo comprobar si hay actualizaciones disponibles",
    )
    parser.add_argument(
        "--apply", "--ejecutar", dest="apply", action="store_true",
        help="Aplicar las actualizaciones (obligatorio para ejecutar la actualización real)",
    )
    args = parser.parse_args()

    # Sin ningún flag de acción: mostrar ayuda y salir limpio
    if not args.dry_run and not args.check and not args.apply:
        parser.print_help()
        sys.exit(0)

    # Los flags de acción son mutuamente excluyentes
    flags_activos = sum([args.dry_run, args.check, args.apply])
    if flags_activos > 1:
        parser.error(
            "--dry-run, --check y --apply son mutuamente excluyentes; "
            "usa solo uno a la vez"
        )

    config = cargar_config(RUTA_AUTO)
    tareas = config.get("tareas", {})
    red = config.get("red", {})
    notif_config = config.get("notificaciones", {})
    solo_si_cambios = notif_config.get("solo_si_hay_cambios", True)

    modo = "[DRY-RUN] " if args.dry_run else "[CHECK] " if args.check else ""
    logger.info(f"=== {modo}Inicio de actualización ===")

    host = red.get("host_ping", "1.1.1.1")
    timeout = red.get("timeout_ping", 5)
    if not hay_conexion(host, timeout):
        logger.info("Sin conexión a internet, abortando")
        return

    if not args.check and not args.dry_run and pacman_bloqueado():
        notificar(
            "actualizador",
            "Pacman está en uso por otro proceso. Reintentaré más tarde.",
            "aviso", 5000,
        )
        return

    if args.check:
        pacman_pending = listar_actualizaciones_pacman() if tareas.get("pacman", True) else []
        aur_pending = listar_actualizaciones_aur() if tareas.get("aur", True) else []
        total = len(pacman_pending) + len(aur_pending)

        if total == 0:
            logger.info("Sistema al día, sin actualizaciones pendientes")
        else:
            logger.info(f"Actualizaciones disponibles: {len(pacman_pending)} pacman, {len(aur_pending)} AUR")
            for p in pacman_pending:
                logger.info(f"  pacman: {p}")
            for p in aur_pending:
                logger.info(f"  AUR: {p}")
            notificar(
                "actualizador",
                f"Hay {total} actualizaciones pendientes ({len(pacman_pending)} sistema, {len(aur_pending)} AUR)",
                "info", 8000,
            )
        return

    pacman_pending = listar_actualizaciones_pacman() if tareas.get("pacman", True) else []
    aur_pending = listar_actualizaciones_aur() if tareas.get("aur", True) else []

    if not pacman_pending and not aur_pending:
        logger.info("Sin actualizaciones pendientes")
        return

    if not args.dry_run:
        guardar_snapshot(pacman_pending, aur_pending)

    n_pacman = 0
    n_aur = 0
    errores = []

    if tareas.get("pacman", True) and pacman_pending:
        logger.info(f"{modo}Actualizando {len(pacman_pending)} paquetes del sistema...")
        ok, n = actualizar_pacman(args.dry_run)
        if ok:
            n_pacman = n
        else:
            errores.append("pacman")

    if tareas.get("aur", True) and aur_pending:
        logger.info(f"{modo}Actualizando {len(aur_pending)} paquetes AUR...")
        ok, n = actualizar_aur(args.dry_run)
        if ok:
            n_aur = n
        else:
            errores.append("AUR")

    total = n_pacman + n_aur
    logger.info(f"=== {modo}Actualización completada: {n_pacman} sistema, {n_aur} AUR ===")

    if errores:
        notificar(
            "actualizador",
            f"Actualización con errores en: {', '.join(errores)}",
            "error",
        )
        sys.exit(1)
    elif total > 0 or not solo_si_cambios:
        notificar(
            "actualizador",
            f"{modo}Actualizados {n_pacman} paquetes del sistema y {n_aur} de AUR",
            "exito", 8000,
        )
    else:
        logger.info("Sin actualizaciones, no se notifica al usuario")


if __name__ == "__main__":
    main()
