#!/usr/bin/env python3
"""
Guardian de Secretos — Cifrador de Archivos Sensibles
Cifra y descifra archivos con GPG simetrico + contrasena maestra.
Parte del ecosistema: seguridad.
"""

import argparse
import getpass
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("guardian_secretos")

CONSEJERO = "guardian_secretos"


# -- Utilidades ----------------------------------------------------------------

def _cargar_cfg() -> dict:
    """Carga la configuracion del guardian de secretos."""
    return cargar_config(RUTA_AUTO)


def _ruta_boveda(config: dict) -> Path:
    """Devuelve la ruta de la boveda, creandola si no existe."""
    cfg_boveda = config.get("boveda", {})
    ruta = Path(cfg_boveda.get("ruta", "~/.config/automatizaciones/boveda")).expanduser()
    if not ruta.exists():
        ruta.mkdir(parents=True, exist_ok=True)
        ruta.chmod(0o700)
        logger.info(f"Boveda creada en {ruta}")
    return ruta


def _cipher_algo(config: dict) -> str:
    """Devuelve el algoritmo de cifrado configurado."""
    return config.get("boveda", {}).get("cipher_algo", "AES256")


def _editor(config: dict) -> str:
    """Devuelve el editor configurado (prioridad: $EDITOR > config > nvim)."""
    return os.environ.get("EDITOR", config.get("boveda", {}).get("editor", "nvim"))


def _pedir_contrasena(confirmar: bool = False) -> str:
    """Pide la contrasena maestra al usuario (no se muestra en pantalla)."""
    if not sys.stdin.isatty():
        print("  Error: No se puede leer la contrasena en modo no interactivo", file=sys.stderr)
        sys.exit(1)
    contrasena = getpass.getpass("Contrasena maestra: ")
    if not contrasena:
        raise ValueError("La contrasena no puede estar vacia")
    if confirmar:
        confirmacion = getpass.getpass("Confirmar contrasena: ")
        if contrasena != confirmacion:
            raise ValueError("Las contrasenas no coinciden")
    return contrasena


def _resolver_nombre(nombre: str, boveda: Path) -> Path:
    """Resuelve un nombre a su ruta .gpg dentro de la boveda."""
    if nombre.endswith(".gpg"):
        ruta = boveda / nombre
    else:
        ruta = boveda / f"{nombre}.gpg"
    return ruta


def _cifrar_gpg(ruta_origen: Path, ruta_destino: Path, contrasena: str, cipher_algo: str) -> bool:
    """Cifra un archivo con GPG simetrico. Devuelve True si tuvo exito."""
    try:
        result = subprocess.run(
            [
                "gpg", "--symmetric",
                "--cipher-algo", cipher_algo,
                "--batch", "--yes",
                "--passphrase-fd", "0",
                "--output", str(ruta_destino),
                str(ruta_origen),
            ],
            input=contrasena.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            logger.error(f"GPG cifrado fallo: {stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("GPG cifrado: timeout")
        return False
    except FileNotFoundError:
        logger.error("GPG no encontrado en el sistema")
        return False


def _descifrar_gpg(ruta_gpg: Path, ruta_destino: Path, contrasena: str) -> bool:
    """Descifra un archivo .gpg. Devuelve True si tuvo exito."""
    try:
        result = subprocess.run(
            [
                "gpg", "--decrypt",
                "--batch", "--yes",
                "--passphrase-fd", "0",
                "--output", str(ruta_destino),
                str(ruta_gpg),
            ],
            input=contrasena.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            logger.error(f"GPG descifrado fallo: {stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("GPG descifrado: timeout")
        return False
    except FileNotFoundError:
        logger.error("GPG no encontrado en el sistema")
        return False


def _shred_archivo(ruta: Path) -> bool:
    """Borra un archivo de forma segura con shred. Devuelve True si tuvo exito."""
    if not ruta.exists():
        return True
    try:
        result = subprocess.run(
            ["shred", "-u", str(ruta)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"shred fallo para {ruta}, intentando borrado normal")
            ruta.unlink()
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"shred timeout para {ruta}")
        return False
    except FileNotFoundError:
        # shred no disponible, borrado normal
        logger.warning("shred no encontrado, usando borrado normal")
        try:
            ruta.unlink()
            return True
        except OSError as e:
            logger.error(f"No se pudo borrar {ruta}: {e}")
            return False


# -- Comandos ------------------------------------------------------------------

def cmd_cifrar(archivo: str, config: dict) -> int:
    """Cifra un archivo y lo mueve a la boveda."""
    ruta_origen = Path(archivo).resolve()
    if not ruta_origen.exists():
        logger.error(f"Archivo no encontrado: {ruta_origen}")
        print(f"Error: archivo no encontrado: {ruta_origen}")
        return 1

    if ruta_origen.suffix == ".gpg":
        print("Error: el archivo ya tiene extension .gpg")
        return 1

    boveda = _ruta_boveda(config)
    ruta_destino = boveda / f"{ruta_origen.name}.gpg"

    if ruta_destino.exists():
        print(f"Error: ya existe {ruta_destino.name} en la boveda")
        return 1

    try:
        contrasena = _pedir_contrasena(confirmar=True)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    cipher = _cipher_algo(config)
    logger.info(f"Cifrando {ruta_origen.name}")

    if not _cifrar_gpg(ruta_origen, ruta_destino, contrasena, cipher):
        print("Error: fallo el cifrado GPG")
        notificar(CONSEJERO, f"Fallo al cifrar {ruta_origen.name}", "error")
        return 1

    # Permisos 600 en el archivo cifrado
    ruta_destino.chmod(0o600)

    # Borrado seguro del original
    if not _shred_archivo(ruta_origen):
        logger.warning(f"No se pudo borrar de forma segura el original: {ruta_origen}")
        print(f"Aviso: cifrado correcto pero no se pudo borrar el original")

    logger.info(f"Cifrado completado: {ruta_destino.name}")
    print(f"Cifrado: {ruta_destino.name}")
    cfg_notif = config.get("notificacion", {})
    notificar(
        CONSEJERO,
        f"Archivo cifrado: {ruta_origen.name}",
        cfg_notif.get("severidad", "info"),
        cfg_notif.get("duracion", 3000),
    )
    return 0


def cmd_descifrar(archivo_gpg: str, config: dict) -> int:
    """Descifra un .gpg de la boveda al directorio actual."""
    boveda = _ruta_boveda(config)

    # Buscar en boveda o ruta directa
    ruta_gpg = Path(archivo_gpg)
    if not ruta_gpg.is_absolute():
        candidato_boveda = _resolver_nombre(archivo_gpg, boveda)
        if candidato_boveda.exists():
            ruta_gpg = candidato_boveda
        else:
            ruta_gpg = ruta_gpg.resolve()

    if not ruta_gpg.exists():
        print(f"Error: archivo no encontrado: {ruta_gpg}")
        return 1

    if ruta_gpg.suffix != ".gpg":
        print("Error: el archivo no tiene extension .gpg")
        return 1

    nombre_salida = ruta_gpg.stem
    ruta_destino = Path.cwd() / nombre_salida

    if ruta_destino.exists():
        print(f"Error: ya existe {nombre_salida} en el directorio actual")
        return 1

    try:
        contrasena = _pedir_contrasena()
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    logger.info(f"Descifrando {ruta_gpg.name}")

    if not _descifrar_gpg(ruta_gpg, ruta_destino, contrasena):
        print("Error: fallo el descifrado (contrasena incorrecta?)")
        notificar(CONSEJERO, f"Fallo al descifrar {ruta_gpg.name}", "error")
        return 1

    ruta_destino.chmod(0o600)
    logger.info(f"Descifrado completado: {nombre_salida}")
    print(f"Descifrado: {nombre_salida}")
    cfg_notif = config.get("notificacion", {})
    notificar(
        CONSEJERO,
        f"Archivo descifrado: {nombre_salida}",
        cfg_notif.get("severidad", "info"),
        cfg_notif.get("duracion", 3000),
    )
    return 0


def cmd_abrir(nombre: str, config: dict) -> int:
    """Descifra temporalmente y muestra el contenido en terminal."""
    boveda = _ruta_boveda(config)
    ruta_gpg = _resolver_nombre(nombre, boveda)

    if not ruta_gpg.exists():
        print(f"Error: no se encuentra '{nombre}' en la boveda")
        _listar_disponibles(boveda)
        return 1

    try:
        contrasena = _pedir_contrasena()
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    # Crear temporal seguro
    fd, ruta_tmp = tempfile.mkstemp(prefix="cripta_", suffix=".tmp")
    os.close(fd)
    ruta_tmp = Path(ruta_tmp)
    ruta_tmp.chmod(0o600)

    try:
        if not _descifrar_gpg(ruta_gpg, ruta_tmp, contrasena):
            print("Error: fallo el descifrado (contrasena incorrecta?)")
            return 1

        logger.info(f"Mostrando contenido de {nombre}")
        contenido = ruta_tmp.read_text(encoding="utf-8", errors="replace")
        print(f"\n--- {nombre} ---")
        print(contenido)
        print(f"--- fin de {nombre} ---\n")
        return 0
    finally:
        _shred_archivo(ruta_tmp)


def cmd_editar(nombre: str, config: dict) -> int:
    """Descifra a temporal, abre en editor, re-cifra al cerrar."""
    boveda = _ruta_boveda(config)
    ruta_gpg = _resolver_nombre(nombre, boveda)

    if not ruta_gpg.exists():
        print(f"Error: no se encuentra '{nombre}' en la boveda")
        _listar_disponibles(boveda)
        return 1

    try:
        contrasena = _pedir_contrasena()
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    # Crear temporal seguro
    fd, ruta_tmp = tempfile.mkstemp(prefix="cripta_", suffix=".tmp")
    os.close(fd)
    ruta_tmp = Path(ruta_tmp)
    ruta_tmp.chmod(0o600)

    try:
        if not _descifrar_gpg(ruta_gpg, ruta_tmp, contrasena):
            print("Error: fallo el descifrado (contrasena incorrecta?)")
            return 1

        editor_cmd = _editor(config)
        logger.info(f"Editando {nombre} con {editor_cmd}")

        try:
            result = subprocess.run([editor_cmd, str(ruta_tmp)])
        except FileNotFoundError:
            print(f"Error: editor '{editor_cmd}' no encontrado")
            logger.error(f"Editor no encontrado: {editor_cmd}")
            return 1

        if result.returncode != 0:
            logger.warning(f"Editor salio con codigo {result.returncode}")
            print(f"Aviso: el editor salio con codigo {result.returncode}")

        # Re-cifrar el archivo editado
        cipher = _cipher_algo(config)
        ruta_gpg_nueva = ruta_gpg.with_suffix(".gpg.new")

        if not _cifrar_gpg(ruta_tmp, ruta_gpg_nueva, contrasena, cipher):
            print("Error: fallo al re-cifrar. El archivo original no se ha modificado.")
            logger.error(f"Fallo al re-cifrar {nombre}")
            if ruta_gpg_nueva.exists():
                ruta_gpg_nueva.unlink()
            return 1

        # Reemplazar el original con el nuevo
        ruta_gpg_nueva.replace(ruta_gpg)
        ruta_gpg.chmod(0o600)

        logger.info(f"Edicion completada y re-cifrada: {nombre}")
        print(f"Guardado y cifrado: {nombre}")
        cfg_notif = config.get("notificacion", {})
        notificar(
            CONSEJERO,
            f"Archivo editado y re-cifrado: {nombre}",
            cfg_notif.get("severidad", "info"),
            cfg_notif.get("duracion", 3000),
        )
        return 0
    except KeyboardInterrupt:
        logger.warning(f"Edicion interrumpida para {nombre}")
        print("\nEdicion interrumpida. Limpiando temporal...")
        return 1
    finally:
        _shred_archivo(ruta_tmp)


def cmd_estado(config: dict) -> int:
    """Lista todos los archivos en la boveda con tamano y fecha."""
    boveda = _ruta_boveda(config)
    archivos = sorted(boveda.glob("*.gpg"))

    if not archivos:
        print("La boveda esta vacia.")
        return 0

    print(f"\n  Boveda: {boveda}")
    print(f"  {'=' * 55}\n")
    print(f"  {'Archivo':<30} {'Tamano':>10} {'Modificado':>15}")
    print(f"  {'-' * 30} {'-' * 10} {'-' * 15}")

    for archivo in archivos:
        stat = archivo.stat()
        nombre = archivo.stem
        tamano = _formato_tamano(stat.st_size)
        import time
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        print(f"  {nombre:<30} {tamano:>10} {fecha:>15}")

    print(f"\n  Total: {len(archivos)} archivo(s)\n")
    return 0


def _formato_tamano(bytes_n: int) -> str:
    """Formatea bytes a formato legible."""
    for unidad in ("B", "KB", "MB", "GB"):
        if bytes_n < 1024:
            return f"{bytes_n:.0f} {unidad}" if unidad == "B" else f"{bytes_n:.1f} {unidad}"
        bytes_n /= 1024
    return f"{bytes_n:.1f} TB"


def _listar_disponibles(boveda: Path):
    """Muestra los archivos disponibles en la boveda (ayuda cuando no se encuentra uno)."""
    archivos = sorted(boveda.glob("*.gpg"))
    if archivos:
        print("\nArchivos disponibles en la boveda:")
        for a in archivos:
            print(f"  - {a.stem}")
        print()


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="cripta",
        description="Guardian de Secretos — Cifrador de archivos sensibles",
    )
    subparsers = parser.add_subparsers(dest="comando", help="Comando a ejecutar")

    # cripta abrir <nombre>
    p_abrir = subparsers.add_parser("abrir", help="Descifra y muestra contenido en terminal")
    p_abrir.add_argument("nombre", help="Nombre del archivo en la boveda")

    # cripta editar <nombre>
    p_editar = subparsers.add_parser("editar", help="Descifra, abre en editor, re-cifra al cerrar")
    p_editar.add_argument("nombre", help="Nombre del archivo en la boveda")

    # cripta cifrar <archivo>
    p_cifrar = subparsers.add_parser("cifrar", help="Cifra un archivo y lo guarda en la boveda")
    p_cifrar.add_argument("archivo", help="Ruta del archivo a cifrar")

    # cripta descifrar <archivo>
    p_descifrar = subparsers.add_parser("descifrar", help="Descifra un .gpg al directorio actual")
    p_descifrar.add_argument("archivo", help="Nombre o ruta del archivo .gpg")

    # cripta estado
    subparsers.add_parser("estado", help="Lista archivos en la boveda")

    # cripta lista (alias de estado)
    subparsers.add_parser("lista", help="Lista archivos en la boveda (alias de estado)")

    args = parser.parse_args()

    if not args.comando:
        parser.print_help()
        return 1

    config = _cargar_cfg()

    comandos = {
        "abrir": lambda: cmd_abrir(args.nombre, config),
        "editar": lambda: cmd_editar(args.nombre, config),
        "cifrar": lambda: cmd_cifrar(args.archivo, config),
        "descifrar": lambda: cmd_descifrar(args.archivo, config),
        "estado": lambda: cmd_estado(config),
        "lista": lambda: cmd_estado(config),
    }

    return comandos[args.comando]()


if __name__ == "__main__":
    sys.exit(main())
