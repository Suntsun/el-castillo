#!/usr/bin/env python3
"""
Invocador de Entorno — Lanzador de Modos de Trabajo
Abre de golpe todas las apps de un perfil configurable en TOML.
Parte del ecosistema: productividad / orquestación.
"""

import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_MODOS = RUTA_AUTO / "modos"
RUTA_PLANTILLA = RUTA_MODOS / "plantilla.toml"
RUTA_MODO_ACTIVO = Path("/tmp/invocador_modo_activo.json")

logger = configurar_logger("invocador_entorno")


# -- Carga de modos --------------------------------------------------------

def cargar_modo(nombre: str) -> dict:
    """Carga un modo desde su archivo TOML en modos/."""
    ruta = RUTA_MODOS / f"{nombre}.toml"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el modo: {ruta}")
    with open(ruta, "rb") as f:
        return tomllib.load(f)


def listar_modos() -> list[dict]:
    """Devuelve lista de modos disponibles con nombre y descripcion."""
    modos = []
    if not RUTA_MODOS.exists():
        return modos
    for archivo in sorted(RUTA_MODOS.glob("*.toml")):
        if archivo.name == "plantilla.toml":
            continue
        try:
            with open(archivo, "rb") as f:
                datos = tomllib.load(f)
            info = datos.get("modo", {})
            modos.append({
                "archivo": archivo.name,
                "nombre": info.get("nombre", archivo.stem),
                "descripcion": info.get("descripcion", ""),
            })
        except Exception as e:
            logger.warning(f"Error leyendo {archivo.name}: {e}")
    return modos


# -- Lanzamiento de apps ---------------------------------------------------

def _necesita_shell(comando: str) -> bool:
    """Detecta si un comando necesita shell=True (espacios, pipes, URLs)."""
    metacaracteres = ("|", "&&", "||", ";", ">", "<", "$", "`", "~")
    if any(c in comando for c in metacaracteres):
        return True
    # Comandos con argumentos (mas de una palabra) necesitan shell
    partes = comando.strip().split()
    if len(partes) > 1:
        return True
    return False


def cambiar_workspace(numero: int) -> bool:
    """Cambia al workspace indicado en Hyprland."""
    try:
        resultado = subprocess.run(
            ["hyprctl", "dispatch", "workspace", str(numero)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if resultado.returncode != 0:
            logger.warning(f"Error cambiando a workspace {numero}: {resultado.stderr}")
            return False
        return True
    except FileNotFoundError:
        logger.warning("hyprctl no disponible — no se puede cambiar workspace")
        return False
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout cambiando a workspace {numero}")
        return False


def lanzar_app(comando: str) -> int | None:
    """Lanza una app y devuelve su PID, o None si falla."""
    try:
        if _necesita_shell(comando):
            proc = subprocess.Popen(
                comando,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            proc = subprocess.Popen(
                [comando],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        logger.info(f"Lanzado: {comando} (PID {proc.pid})")
        return proc.pid
    except Exception as e:
        logger.error(f"Error lanzando '{comando}': {e}")
        return None


def lanzar_modo(nombre: str) -> dict:
    """Lanza todas las apps de un modo. Devuelve info del modo activo."""
    datos = cargar_modo(nombre)
    apps = datos.get("apps", [])
    info_modo = datos.get("modo", {})

    if not apps:
        logger.warning(f"Modo '{nombre}' no tiene apps configuradas")
        return {"modo": nombre, "pids": [], "timestamp": datetime.now().isoformat()}

    pids = []
    for i, app in enumerate(apps):
        comando = app.get("comando", "")
        if not comando:
            logger.warning(f"App #{i+1} sin comando, saltando")
            continue

        workspace = app.get("workspace")
        if workspace is not None:
            cambiar_workspace(workspace)
            time.sleep(0.3)

        pid = lanzar_app(comando)
        if pid is not None:
            pids.append(pid)

        delay = app.get("delay", 0)
        if delay > 0:
            time.sleep(delay)

    estado = {
        "modo": nombre,
        "pids": pids,
        "timestamp": datetime.now().isoformat(),
    }
    guardar_modo_activo(estado)
    return estado


# -- Modo activo ------------------------------------------------------------

def guardar_modo_activo(estado: dict):
    """Guarda el estado del modo activo en /tmp."""
    try:
        RUTA_MODO_ACTIVO.write_text(json.dumps(estado, indent=2), encoding="utf-8")
        logger.info(f"Estado guardado en {RUTA_MODO_ACTIVO}")
    except Exception as e:
        logger.error(f"Error guardando estado: {e}")


def cargar_modo_activo() -> dict | None:
    """Carga el estado del modo activo, o None si no hay."""
    if not RUTA_MODO_ACTIVO.exists():
        return None
    try:
        return json.loads(RUTA_MODO_ACTIVO.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error leyendo modo activo: {e}")
        return None


def parar_modo() -> str | None:
    """Cierra las apps del modo activo. Devuelve nombre del modo o None."""
    estado = cargar_modo_activo()
    if estado is None:
        logger.info("No hay modo activo que parar")
        return None

    nombre = estado.get("modo", "desconocido")
    pids = estado.get("pids", [])
    cerrados = 0

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            cerrados += 1
            logger.info(f"SIGTERM enviado a PID {pid}")
        except ProcessLookupError:
            logger.info(f"PID {pid} ya no existe")
        except PermissionError:
            logger.warning(f"Sin permisos para matar PID {pid}")

    try:
        RUTA_MODO_ACTIVO.unlink()
    except OSError:
        pass

    logger.info(f"Modo '{nombre}' parado ({cerrados}/{len(pids)} procesos cerrados)")
    return nombre


# -- Crear nuevo modo -------------------------------------------------------

def crear_modo(nombre: str) -> Path:
    """Crea un nuevo modo copiando la plantilla. Devuelve la ruta."""
    destino = RUTA_MODOS / f"{nombre}.toml"
    if destino.exists():
        raise FileExistsError(f"El modo '{nombre}' ya existe: {destino}")

    if not RUTA_PLANTILLA.exists():
        raise FileNotFoundError(f"Plantilla no encontrada: {RUTA_PLANTILLA}")

    contenido = RUTA_PLANTILLA.read_text(encoding="utf-8")
    contenido = contenido.replace('nombre = "mi_modo"', f'nombre = "{nombre}"')
    destino.write_text(contenido, encoding="utf-8")
    logger.info(f"Modo '{nombre}' creado en {destino}")
    return destino


def editar_modo(nombre: str):
    """Abre el TOML del modo en $EDITOR."""
    ruta = RUTA_MODOS / f"{nombre}.toml"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el modo: {ruta}")

    editor = os.environ.get("EDITOR", "nano")
    logger.info(f"Abriendo {ruta} con {editor}")
    subprocess.run([editor, str(ruta)])


# -- CLI principal -----------------------------------------------------------

def mostrar_ayuda():
    """Muestra la ayuda del comando."""
    ayuda = """modo — Lanzador de Modos de Trabajo

Uso:
  modo <nombre>           Lanza el modo (abre todas las apps del perfil)
  modo lista              Lista modos disponibles con descripcion
  modo parar              Cierra las apps del modo activo actual
  modo nuevo <nombre>     Crea un modo vacio desde plantilla
  modo editar <nombre>    Abre el TOML del modo en $EDITOR

Ejemplos:
  modo dev                Abre Opera, 2 terminales y YouTube
  modo musica             Abre lofi stream y carpeta de musica
  modo nuevo trabajo      Crea modos/trabajo.toml para editar
  modo parar              Cierra todo lo que abrio el modo activo"""
    print(ayuda)


def main():
    config = cargar_config(RUTA_AUTO)
    cfg_notif = config.get("notificacion", {})
    duracion = cfg_notif.get("duracion", 3000)
    severidad = cfg_notif.get("severidad", "info")

    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "ayuda"):
        mostrar_ayuda()
        return

    comando = args[0]

    # -- modo lista --
    if comando == "lista":
        modos = listar_modos()
        if not modos:
            print("No hay modos disponibles en modos/")
            return
        print(f"{'Nombre':<20} Descripcion")
        print("-" * 60)
        for m in modos:
            print(f"{m['nombre']:<20} {m['descripcion']}")
        return

    # -- modo parar --
    if comando == "parar":
        nombre = parar_modo()
        if nombre:
            notificar(
                "invocador_entorno",
                f"Modo {nombre} cerrado",
                severidad,
                duracion,
            )
            print(f"Modo '{nombre}' cerrado")
        else:
            print("No hay modo activo que parar")
        return

    # -- modo nuevo <nombre> --
    if comando == "nuevo":
        if len(args) < 2:
            print("Uso: modo nuevo <nombre>")
            sys.exit(1)
        nombre = args[1]
        try:
            ruta = crear_modo(nombre)
            print(f"Modo '{nombre}' creado en {ruta}")
            print(f"Editalo con: modo editar {nombre}")
        except (FileExistsError, FileNotFoundError) as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    # -- modo editar <nombre> --
    if comando == "editar":
        if len(args) < 2:
            print("Uso: modo editar <nombre>")
            sys.exit(1)
        nombre = args[1]
        try:
            editar_modo(nombre)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    # -- modo <nombre> — lanzar --
    nombre = comando
    try:
        estado = lanzar_modo(nombre)
        n_apps = len(estado["pids"])
        notificar(
            "invocador_entorno",
            f"Modo {nombre} activado — {n_apps} apps",
            severidad,
            duracion,
        )
        print(f"Modo '{nombre}' activado — {n_apps} apps lanzadas")
    except FileNotFoundError:
        print(f"Error: modo '{nombre}' no encontrado")
        print(f"Modos disponibles: {', '.join(m['nombre'] for m in listar_modos())}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error lanzando modo '{nombre}': {e}")
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
