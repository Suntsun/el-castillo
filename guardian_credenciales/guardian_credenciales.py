#!/usr/bin/env python3
"""
Guardian de Credenciales — Gestor de API keys y credenciales
Acceso rapido desde terminal, copia al portapapeles, y listado con estado.
Parte del ecosistema: seguridad.
"""

import argparse
import getpass
import os
import stat
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("guardian_credenciales")

CONSEJERO = "guardian_credenciales"

# -- Colores ANSI --------------------------------------------------------------

_C = {
    "verde": "\033[32m",
    "rojo": "\033[31m",
    "amarillo": "\033[33m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


# -- Utilidades ----------------------------------------------------------------

def _ruta_apis(config: dict) -> Path:
    """Devuelve la ruta al directorio de APIs, creandolo si no existe."""
    ruta_str = config.get("almacenamiento", {}).get("ruta", "~/.config/automatizaciones/apis")
    ruta = Path(ruta_str).expanduser()
    if not ruta.exists():
        ruta.mkdir(parents=True, exist_ok=True)
        os.chmod(ruta, stat.S_IRWXU)  # 700
        logger.info(f"Directorio de APIs creado: {ruta}")
    return ruta


def _ruta_api(directorio: Path, nombre: str) -> Path:
    """Devuelve la ruta al archivo TOML de una API concreta."""
    return directorio / f"{nombre}.toml"


def enmascarar_key(key: str) -> str:
    """Muestra solo los primeros 4 y ultimos 4 caracteres de una key."""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


def _cargar_api(ruta: Path) -> dict:
    """Carga y devuelve el contenido de un archivo TOML de API."""
    with open(ruta, "rb") as f:
        return tomllib.load(f)


def _escribir_toml_api(ruta: Path, key: str, descripcion: str):
    """Escribe un archivo TOML de API con formato correcto."""
    contenido = (
        "[api]\n"
        f'key = "{key}"\n'
        f'descripcion = "{descripcion}"\n'
        f'fecha_alta = "{date.today().isoformat()}"\n'
    )
    ruta.write_text(contenido, encoding="utf-8")
    os.chmod(ruta, stat.S_IRUSR | stat.S_IWUSR)  # 600
    logger.info(f"API guardada: {ruta.stem}")


# -- Comandos ------------------------------------------------------------------

def copiar_key(nombre: str, config: dict) -> int:
    """Copia la API key al portapapeles con wl-copy."""
    directorio = _ruta_apis(config)
    ruta = _ruta_api(directorio, nombre)

    if not ruta.exists():
        print(f"{_C['rojo']}API '{nombre}' no encontrada{_C['reset']}")
        logger.warning(f"API no encontrada: {nombre}")
        return 1

    datos = _cargar_api(ruta)
    key = datos.get("api", {}).get("key", "")
    if not key:
        print(f"{_C['rojo']}API '{nombre}' no tiene key definida{_C['reset']}")
        logger.error(f"API sin key: {nombre}")
        return 1

    try:
        proc = subprocess.run(
            ["wl-copy", "--", key],
            capture_output=True, timeout=5,
        )
        if proc.returncode != 0:
            print(f"{_C['rojo']}Error al copiar al portapapeles{_C['reset']}")
            logger.error(f"wl-copy fallo con codigo {proc.returncode}")
            return 1
    except FileNotFoundError:
        print(f"{_C['rojo']}wl-copy no encontrado. Instala wl-clipboard.{_C['reset']}")
        logger.error("wl-copy no disponible")
        return 1
    except subprocess.TimeoutExpired:
        print(f"{_C['rojo']}Timeout al copiar al portapapeles{_C['reset']}")
        logger.error("wl-copy timeout")
        return 1

    desc = datos.get("api", {}).get("descripcion", nombre)
    msg = f"{desc} copiada al portapapeles"
    print(f"{_C['verde']}{msg}{_C['reset']}  {_C['dim']}({enmascarar_key(key)}){_C['reset']}")
    logger.info(f"Key copiada: {nombre} ({enmascarar_key(key)})")

    cfg_notif = config.get("notificacion", {})
    notificar(
        CONSEJERO, msg,
        cfg_notif.get("severidad", "info"),
        cfg_notif.get("duracion", 3000),
    )
    return 0


def ver_key(nombre: str, config: dict) -> int:
    """Muestra la API key en terminal sin copiar."""
    directorio = _ruta_apis(config)
    ruta = _ruta_api(directorio, nombre)

    if not ruta.exists():
        print(f"{_C['rojo']}API '{nombre}' no encontrada{_C['reset']}")
        logger.warning(f"API no encontrada: {nombre}")
        return 1

    datos = _cargar_api(ruta)
    key = datos.get("api", {}).get("key", "")
    desc = datos.get("api", {}).get("descripcion", nombre)

    if not key:
        print(f"{_C['rojo']}API '{nombre}' no tiene key definida{_C['reset']}")
        return 1

    print(f"{_C['bold']}{desc}{_C['reset']}")
    print(f"{_C['dim']}Key:{_C['reset']} {key}")
    logger.info(f"Key mostrada en terminal: {nombre} ({enmascarar_key(key)})")
    return 0


def listar_apis(config: dict) -> int:
    """Lista todas las APIs disponibles con nombre y descripcion."""
    directorio = _ruta_apis(config)
    archivos = sorted(directorio.glob("*.toml"))

    if not archivos:
        print(f"{_C['dim']}No hay APIs guardadas{_C['reset']}")
        return 0

    print(f"\n{_C['bold']}  APIs disponibles{_C['reset']}")
    print(f"{_C['dim']}{'=' * 50}{_C['reset']}\n")

    for archivo in archivos:
        try:
            datos = _cargar_api(archivo)
            nombre = archivo.stem
            desc = datos.get("api", {}).get("descripcion", "")
            key = datos.get("api", {}).get("key", "")
            tiene_check = "verificacion" in datos
            icono_check = f" {_C['dim']}[check]{_C['reset']}" if tiene_check else ""
            print(
                f"  {_C['verde']}{nombre:20s}{_C['reset']} "
                f"{desc}  {_C['dim']}({enmascarar_key(key)}){_C['reset']}"
                f"{icono_check}"
            )
        except Exception as e:
            print(f"  {_C['rojo']}{archivo.stem:20s}{_C['reset']} Error al leer: {e}")
            logger.error(f"Error leyendo {archivo}: {e}")

    print()
    return 0


def anadir_api(nombre: str, config: dict) -> int:
    """Pide la key interactivamente y la guarda como archivo TOML."""
    directorio = _ruta_apis(config)
    ruta = _ruta_api(directorio, nombre)

    if ruta.exists():
        respuesta = input(f"API '{nombre}' ya existe. Sobreescribir? [s/N]: ").strip().lower()
        if respuesta != "s":
            print("Cancelado")
            return 0

    try:
        key = getpass.getpass(f"Key para '{nombre}': ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado")
        return 0

    if not key.strip():
        print(f"{_C['rojo']}Key vacia, cancelado{_C['reset']}")
        return 1

    descripcion = input(f"Descripcion (opcional): ").strip()
    if not descripcion:
        descripcion = nombre

    _escribir_toml_api(ruta, key.strip(), descripcion)

    print(f"{_C['verde']}API '{nombre}' guardada{_C['reset']}  {_C['dim']}({enmascarar_key(key.strip())}){_C['reset']}")

    cfg_notif = config.get("notificacion", {})
    notificar(
        CONSEJERO, f"API '{nombre}' guardada",
        cfg_notif.get("severidad", "info"),
        cfg_notif.get("duracion", 3000),
    )
    return 0


def borrar_api(nombre: str, config: dict) -> int:
    """Elimina una API key con confirmacion."""
    directorio = _ruta_apis(config)
    ruta = _ruta_api(directorio, nombre)

    if not ruta.exists():
        print(f"{_C['rojo']}API '{nombre}' no encontrada{_C['reset']}")
        return 1

    respuesta = input(f"Eliminar API '{nombre}'? [s/N]: ").strip().lower()
    if respuesta != "s":
        print("Cancelado")
        return 0

    ruta.unlink()
    print(f"{_C['amarillo']}API '{nombre}' eliminada{_C['reset']}")
    logger.info(f"API eliminada: {nombre}")

    cfg_notif = config.get("notificacion", {})
    notificar(
        CONSEJERO, f"API '{nombre}' eliminada",
        "aviso",
        cfg_notif.get("duracion", 3000),
    )
    return 0


def verificar_apis(config: dict) -> int:
    """Comprueba estado de APIs que tienen endpoint de verificacion."""
    directorio = _ruta_apis(config)
    archivos = sorted(directorio.glob("*.toml"))

    if not archivos:
        print(f"{_C['dim']}No hay APIs guardadas{_C['reset']}")
        return 0

    apis_con_check = []
    for archivo in archivos:
        try:
            datos = _cargar_api(archivo)
            if "verificacion" in datos:
                apis_con_check.append((archivo.stem, datos))
        except Exception as e:
            logger.error(f"Error leyendo {archivo}: {e}")

    if not apis_con_check:
        print(f"{_C['dim']}Ninguna API tiene verificacion configurada{_C['reset']}")
        return 0

    print(f"\n{_C['bold']}  Verificacion de APIs{_C['reset']}")
    print(f"{_C['dim']}{'=' * 50}{_C['reset']}\n")

    fallos = 0
    for nombre, datos in apis_con_check:
        key = datos.get("api", {}).get("key", "")
        verif = datos["verificacion"]
        url = verif.get("url", "")
        header_tpl = verif.get("header", "")
        codigo_ok = verif.get("codigo_ok", 200)

        if not url:
            print(f"  {_C['amarillo']}[!!]{_C['reset']}  {nombre:20s} Sin URL de verificacion")
            continue

        ok = _verificar_una_api(url, header_tpl, key, codigo_ok)
        if ok:
            print(f"  {_C['verde']}[OK]{_C['reset']}  {nombre:20s} Activa")
            logger.info(f"Verificacion OK: {nombre}")
        else:
            print(f"  {_C['rojo']}[XX]{_C['reset']}  {nombre:20s} Fallo")
            logger.warning(f"Verificacion fallida: {nombre}")
            fallos += 1

    print()

    if fallos:
        cfg_notif = config.get("notificacion", {})
        msg = f"{fallos} API(s) con problemas"
        notificar(CONSEJERO, msg, "aviso", cfg_notif.get("duracion", 3000))
        return 1

    return 0


def _verificar_una_api(url: str, header_tpl: str, key: str, codigo_ok: int) -> bool:
    """Hace un request HTTP para verificar que una API responde correctamente."""
    try:
        req = urllib.request.Request(url)

        if header_tpl and key:
            header_valor = header_tpl.replace("{key}", key)
            # Formato: "NombreHeader: valor"
            if ": " in header_valor:
                h_nombre, h_valor = header_valor.split(": ", 1)
                req.add_header(h_nombre, h_valor)

        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == codigo_ok
    except urllib.error.HTTPError as e:
        logger.debug(f"HTTP {e.code} al verificar {url}")
        return e.code == codigo_ok
    except Exception as e:
        logger.debug(f"Error al verificar {url}: {e}")
        return False


# -- CLI -----------------------------------------------------------------------

def main():
    config = cargar_config(RUTA_AUTO)

    parser = argparse.ArgumentParser(
        prog="api",
        description="Gestor de API keys y credenciales",
    )
    parser.add_argument(
        "comando",
        nargs="?",
        help="Nombre de la API a copiar, o: lista, añadir, borrar, check",
    )
    parser.add_argument(
        "nombre",
        nargs="?",
        help="Nombre de la API (para añadir/borrar)",
    )
    parser.add_argument(
        "--ver", action="store_true",
        help="Muestra la key en terminal sin copiar",
    )

    args = parser.parse_args()

    if not args.comando:
        parser.print_help()
        return 0

    cmd = args.comando.lower()

    if cmd == "lista":
        return listar_apis(config)

    if cmd in ("añadir", "anadir"):
        if not args.nombre:
            print(f"{_C['rojo']}Uso: api añadir <nombre>{_C['reset']}")
            return 1
        return anadir_api(args.nombre, config)

    if cmd == "borrar":
        if not args.nombre:
            print(f"{_C['rojo']}Uso: api borrar <nombre>{_C['reset']}")
            return 1
        return borrar_api(args.nombre, config)

    if cmd == "check":
        return verificar_apis(config)

    # Si no es un comando especial, es un nombre de API
    nombre_api = cmd
    if args.ver:
        return ver_key(nombre_api, config)
    return copiar_key(nombre_api, config)


if __name__ == "__main__":
    sys.exit(main())
