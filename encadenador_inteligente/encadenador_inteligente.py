#!/usr/bin/env python3
"""
Encadenador Inteligente — Encadenador de Automatizaciones
Cuando una automatizacion termina exitosamente, ejecuta la siguiente
segun las cadenas definidas en TOML.
Parte del ecosistema: orquestacion.
"""

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, RUTA_LOGS

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_CADENAS = RUTA_AUTO / "cadenas"

logger = configurar_logger("encadenador_inteligente")

CONSEJERO = "encadenador_inteligente"

# Regex para parsear lineas del formato estandar de logs del ecosistema
# Formato: "2026-05-26 14:01:09,242 | INFO | actualizador | mensaje aqui"
RE_LINEA_LOG = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \| (\w+) \| (\w+) \| (.+)$"
)


# -- Carga de cadenas ---------------------------------------------------------

def cargar_cadena(ruta: Path) -> dict | None:
    """Carga una cadena desde un fichero TOML.

    Devuelve un dict con 'nombre', 'descripcion' y 'pasos', o None si falla.
    Cada paso tiene: trigger, patron, ejecutar, delay.
    """
    try:
        contenido = ruta.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"No se pudo leer cadena {ruta.name}: {e}")
        return None

    return parsear_cadena_toml(contenido, ruta.name)


def parsear_cadena_toml(contenido: str, nombre_fichero: str = "<string>") -> dict | None:
    """Parsea el contenido TOML de una cadena sin dependencias externas.

    Soporta el subconjunto necesario: [cadena] con claves simples
    y [[pasos]] con claves string/int.
    """
    cadena: dict = {"nombre": "", "descripcion": "", "pasos": []}
    seccion_actual: str | None = None
    paso_actual: dict | None = None

    for num_linea, linea in enumerate(contenido.splitlines(), 1):
        linea_strip = linea.strip()

        # Lineas vacias y comentarios
        if not linea_strip or linea_strip.startswith("#"):
            continue

        # Seccion [[pasos]]
        if linea_strip == "[[pasos]]":
            if paso_actual is not None:
                cadena["pasos"].append(paso_actual)
            paso_actual = {"trigger": "", "patron": "", "ejecutar": "", "delay": 0}
            seccion_actual = "pasos"
            continue

        # Seccion [cadena]
        if linea_strip == "[cadena]":
            seccion_actual = "cadena"
            continue

        # Clave = valor
        if "=" in linea_strip:
            clave, _, valor = linea_strip.partition("=")
            clave = clave.strip()
            valor = valor.strip()

            # Quitar comillas
            if valor.startswith('"') and valor.endswith('"'):
                valor = valor[1:-1]

            # Intentar convertir a int
            valor_final: str | int = valor
            try:
                valor_final = int(valor)
            except ValueError:
                pass

            if seccion_actual == "cadena":
                cadena[clave] = valor_final
            elif seccion_actual == "pasos" and paso_actual is not None:
                paso_actual[clave] = valor_final

    # Ultimo paso pendiente
    if paso_actual is not None:
        cadena["pasos"].append(paso_actual)

    if not cadena["nombre"]:
        logger.warning(f"Cadena sin nombre en {nombre_fichero}")
        return None

    if not cadena["pasos"]:
        logger.warning(f"Cadena '{cadena['nombre']}' sin pasos en {nombre_fichero}")
        return None

    return cadena


def cargar_todas_las_cadenas(ruta_cadenas: Path | None = None) -> list[dict]:
    """Carga todas las cadenas desde el directorio de cadenas.

    Devuelve lista de dicts con la estructura de cadena.
    """
    directorio = ruta_cadenas or RUTA_CADENAS
    if not directorio.exists():
        logger.warning(f"Directorio de cadenas no existe: {directorio}")
        return []

    cadenas = []
    for fichero in sorted(directorio.glob("*.toml")):
        cadena = cargar_cadena(fichero)
        if cadena:
            cadenas.append(cadena)
            logger.debug(
                f"Cadena cargada: '{cadena['nombre']}' "
                f"({len(cadena['pasos'])} pasos)"
            )

    return cadenas


# -- Gestion de posiciones (mismo patron que cronista_errores) ----------------

def cargar_posiciones(ruta: str | Path) -> dict[str, int]:
    """Carga las posiciones (byte offsets) guardadas de cada log."""
    ruta = Path(ruta)
    if not ruta.exists():
        return {}
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"No se pudieron cargar posiciones: {e}")
        return {}


def guardar_posiciones(ruta: str | Path, posiciones: dict[str, int]):
    """Guarda las posiciones actuales de lectura de cada log."""
    ruta = Path(ruta)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(posiciones, f, indent=2)
    except OSError as e:
        logger.error(f"No se pudieron guardar posiciones: {e}")


def inicializar_posiciones(fichero_posiciones: str | Path) -> dict[str, int]:
    """Al primer arranque, registra las posiciones actuales sin disparar cadenas."""
    posiciones = {}
    if not RUTA_LOGS.exists():
        return posiciones

    for log_file in RUTA_LOGS.glob("*.log"):
        if log_file.stem == "encadenador_inteligente":
            continue
        try:
            posiciones[str(log_file)] = log_file.stat().st_size
        except OSError:
            posiciones[str(log_file)] = 0

    guardar_posiciones(fichero_posiciones, posiciones)
    logger.info(f"Posiciones iniciales registradas para {len(posiciones)} logs")
    return posiciones


# -- Escaneo de logs para triggers --------------------------------------------

def escanear_completions(
    posiciones: dict[str, int],
    cadenas: list[dict],
) -> tuple[list[dict], dict[str, int]]:
    """
    Lee las lineas nuevas de todos los logs y busca patrones de exito.

    Devuelve una lista de triggers encontrados y las posiciones actualizadas.
    Cada trigger es un dict con: timestamp, automatizacion, mensaje, cadena, paso.
    """
    triggers = []
    nuevas_posiciones = dict(posiciones)

    if not RUTA_LOGS.exists():
        return triggers, nuevas_posiciones

    # Construir indice de triggers: {nombre_auto: [(patron_compilado, cadena, paso), ...]}
    indice: dict[str, list[tuple[re.Pattern, dict, dict]]] = {}
    for cadena in cadenas:
        for paso in cadena["pasos"]:
            nombre_trigger = paso["trigger"]
            try:
                patron = re.compile(paso["patron"])
            except re.error as e:
                logger.warning(
                    f"Patron regex invalido en cadena '{cadena['nombre']}': "
                    f"'{paso['patron']}' — {e}"
                )
                continue
            indice.setdefault(nombre_trigger, []).append((patron, cadena, paso))

    for log_file in RUTA_LOGS.glob("*.log"):
        if log_file.stem == "encadenador_inteligente":
            continue

        ruta_str = str(log_file)

        try:
            tamano_actual = log_file.stat().st_size
        except OSError:
            continue

        pos_anterior = posiciones.get(ruta_str, 0)

        # Si el archivo se redujo (rotacion de log), leer desde el inicio
        if tamano_actual < pos_anterior:
            pos_anterior = 0

        # Si no hay datos nuevos, saltar
        if tamano_actual <= pos_anterior:
            nuevas_posiciones[ruta_str] = tamano_actual
            continue

        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(pos_anterior)
                contenido = f.read()
                nuevas_posiciones[ruta_str] = f.tell()
        except OSError as e:
            logger.warning(f"No se pudo leer {log_file.name}: {e}")
            continue

        for linea in contenido.splitlines():
            match = RE_LINEA_LOG.match(linea)
            if not match:
                continue

            ts_str, nivel, automatizacion, mensaje = match.groups()

            # Solo buscar en lineas INFO (exito)
            if nivel != "INFO":
                continue

            # Buscar en el indice si esta automatizacion tiene triggers
            if automatizacion not in indice:
                continue

            for patron, cadena, paso in indice[automatizacion]:
                if patron.search(mensaje):
                    triggers.append({
                        "timestamp": ts_str,
                        "automatizacion": automatizacion,
                        "mensaje": mensaje,
                        "cadena_nombre": cadena["nombre"],
                        "paso": paso,
                    })

    return triggers, nuevas_posiciones


# -- Ejecucion de pasos -------------------------------------------------------

def ejecutar_paso(paso: dict) -> dict:
    """Ejecuta un paso de cadena y devuelve el resultado.

    Retorna dict con: exito (bool), comando, inicio, fin, codigo_salida, salida, error.
    """
    comando = paso["ejecutar"]
    delay = paso.get("delay", 0)

    if delay > 0:
        logger.info(f"Esperando {delay}s antes de ejecutar: {comando}")
        time.sleep(delay)

    inicio = datetime.now()
    resultado = {
        "exito": False,
        "comando": comando,
        "inicio": inicio.isoformat(),
        "fin": "",
        "codigo_salida": -1,
        "salida": "",
        "error": "",
    }

    try:
        proc = subprocess.run(
            comando,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        fin = datetime.now()
        resultado["fin"] = fin.isoformat()
        resultado["codigo_salida"] = proc.returncode
        resultado["salida"] = proc.stdout[:500] if proc.stdout else ""
        resultado["error"] = proc.stderr[:500] if proc.stderr else ""
        resultado["exito"] = proc.returncode == 0

        if resultado["exito"]:
            logger.info(f"Paso ejecutado con exito: {comando}")
        else:
            logger.warning(
                f"Paso fallo (codigo {proc.returncode}): {comando} "
                f"— {resultado['error'][:100]}"
            )

    except subprocess.TimeoutExpired:
        fin = datetime.now()
        resultado["fin"] = fin.isoformat()
        resultado["error"] = "Timeout: el comando excedio 600 segundos"
        logger.error(f"Timeout ejecutando: {comando}")

    except OSError as e:
        fin = datetime.now()
        resultado["fin"] = fin.isoformat()
        resultado["error"] = str(e)
        logger.error(f"Error ejecutando paso: {e}")

    return resultado


def ejecutar_cadena_completa(cadena: dict) -> list[dict]:
    """Ejecuta todos los pasos de una cadena en orden.

    Se detiene si un paso falla. Devuelve lista de resultados.
    """
    resultados = []
    logger.info(
        f"Ejecutando cadena '{cadena['nombre']}' "
        f"({len(cadena['pasos'])} pasos)"
    )
    notificar(
        CONSEJERO,
        f"Iniciando cadena: {cadena['nombre']}",
        "info",
        4000,
    )

    for i, paso in enumerate(cadena["pasos"], 1):
        logger.info(f"  Paso {i}/{len(cadena['pasos'])}: {paso['ejecutar']}")
        resultado = ejecutar_paso(paso)
        resultados.append(resultado)

        if not resultado["exito"]:
            logger.warning(
                f"Cadena '{cadena['nombre']}' detenida en paso {i}: "
                f"fallo en {paso['ejecutar']}"
            )
            notificar(
                CONSEJERO,
                f"Cadena '{cadena['nombre']}' fallo en paso {i}",
                "error",
                6000,
            )
            break

    else:
        logger.info(f"Cadena '{cadena['nombre']}' completada con exito")
        notificar(
            CONSEJERO,
            f"Cadena completada: {cadena['nombre']}",
            "exito",
            4000,
        )

    return resultados


# -- Historial ----------------------------------------------------------------

def cargar_historial(ruta: str | Path) -> list[dict]:
    """Carga el historial de ejecuciones."""
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"No se pudo cargar historial: {e}")
        return []


def guardar_historial(
    ruta: str | Path,
    historial: list[dict],
    max_entradas: int = 200,
):
    """Guarda el historial truncado al maximo de entradas."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    # Mantener solo las ultimas entradas
    historial = historial[-max_entradas:]

    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(historial, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"No se pudo guardar historial: {e}")


def registrar_ejecucion(
    ruta_historial: str | Path,
    cadena_nombre: str,
    trigger_info: str,
    resultados: list[dict],
    max_entradas: int = 200,
):
    """Registra una ejecucion de cadena en el historial."""
    historial = cargar_historial(ruta_historial)

    exitos = sum(1 for r in resultados if r["exito"])
    total = len(resultados)

    entrada = {
        "timestamp": datetime.now().isoformat(),
        "cadena": cadena_nombre,
        "trigger": trigger_info,
        "pasos_total": total,
        "pasos_exitosos": exitos,
        "exito": exitos == total,
        "resultados": resultados,
    }

    historial.append(entrada)
    guardar_historial(ruta_historial, historial, max_entradas)


# -- Cooldown para evitar re-disparo ------------------------------------------

_ultimo_disparo: dict[str, float] = {}


def puede_disparar(cadena_nombre: str, cooldown: int) -> bool:
    """Comprueba si ha pasado suficiente tiempo desde el ultimo disparo."""
    ahora = time.time()
    ultimo = _ultimo_disparo.get(cadena_nombre, 0)
    return (ahora - ultimo) >= cooldown


def registrar_disparo(cadena_nombre: str):
    """Registra el momento en que se disparo una cadena."""
    _ultimo_disparo[cadena_nombre] = time.time()


# -- Daemon --------------------------------------------------------------------

_ejecutando = True


def _signal_handler(signum, frame):
    global _ejecutando
    _ejecutando = False
    logger.info("Senal de parada recibida, cerrando...")


def daemon(config: dict):
    """Bucle principal del daemon: escanea logs periodicamente buscando triggers."""
    global _ejecutando

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    cfg_escaneo = config.get("escaneo", {})
    intervalo = cfg_escaneo.get("intervalo", 30)
    fichero_posiciones = cfg_escaneo.get(
        "fichero_posiciones", "/tmp/encadenador_inteligente_posiciones.json"
    )

    cfg_ejecucion = config.get("ejecucion", {})
    cooldown = cfg_ejecucion.get("cooldown", 300)

    cfg_historial = config.get("historial", {})
    ruta_historial = cfg_historial.get(
        "ruta",
        str(RUTA_LOGS / "encadenador_historial.json"),
    )
    max_entradas = cfg_historial.get("max_entradas", 200)

    # Cargar cadenas
    cadenas = cargar_todas_las_cadenas()
    if not cadenas:
        logger.warning("No hay cadenas configuradas, el daemon no tiene nada que hacer")
        notificar(CONSEJERO, "No hay cadenas configuradas", "aviso", 5000)

    # Construir mapa de cadenas por nombre para ejecucion por trigger
    cadenas_por_nombre: dict[str, dict] = {c["nombre"]: c for c in cadenas}

    # Cargar o inicializar posiciones
    posiciones = cargar_posiciones(fichero_posiciones)
    if not posiciones:
        logger.info("Primera ejecucion: registrando posiciones iniciales")
        posiciones = inicializar_posiciones(fichero_posiciones)

    n_triggers = sum(len(c["pasos"]) for c in cadenas)
    logger.info(
        f"Daemon iniciado (intervalo={intervalo}s, "
        f"cadenas={len(cadenas)}, triggers={n_triggers})"
    )
    notificar(
        CONSEJERO,
        f"Encadenador activo: {len(cadenas)} cadenas vigilando",
        "info",
        4000,
    )

    while _ejecutando:
        try:
            triggers, posiciones = escanear_completions(posiciones, cadenas)
            guardar_posiciones(fichero_posiciones, posiciones)

            for trigger in triggers:
                nombre_cadena = trigger["cadena_nombre"]

                if not puede_disparar(nombre_cadena, cooldown):
                    logger.debug(
                        f"Cadena '{nombre_cadena}' en cooldown, saltando"
                    )
                    continue

                registrar_disparo(nombre_cadena)

                cadena = cadenas_por_nombre.get(nombre_cadena)
                if not cadena:
                    continue

                trigger_info = (
                    f"{trigger['automatizacion']}: {trigger['mensaje'][:80]}"
                )
                logger.info(
                    f"Trigger detectado para '{nombre_cadena}': {trigger_info}"
                )

                paso = trigger["paso"]
                resultado = ejecutar_paso(paso)

                registrar_ejecucion(
                    ruta_historial,
                    nombre_cadena,
                    trigger_info,
                    [resultado],
                    max_entradas,
                )

                if resultado["exito"]:
                    notificar(
                        CONSEJERO,
                        f"Cadena '{nombre_cadena}' ejecutada por trigger",
                        "exito",
                        4000,
                    )
                else:
                    notificar(
                        CONSEJERO,
                        f"Fallo en cadena '{nombre_cadena}'",
                        "error",
                        6000,
                    )

        except Exception as e:
            logger.error(f"Error en ciclo de escaneo: {e}")

        # Dormir en intervalos cortos para responder rapido a senales
        for _ in range(intervalo):
            if not _ejecutando:
                break
            time.sleep(1)

    logger.info("Daemon detenido")


# -- CLI: comando 'cadena' ----------------------------------------------------

_COLORES = {
    "rojo": "\033[31m",
    "amarillo": "\033[33m",
    "verde": "\033[32m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def cmd_status(config: dict):
    """Muestra el estado del daemon y cadenas activas."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    verde = _COLORES["verde"]
    rojo = _COLORES["rojo"]
    cyan = _COLORES["cyan"]

    print(f"\n{b}{cyan}  Encadenador Inteligente — Estado{r}")
    print(f"{d}{'=' * 50}{r}\n")

    # Comprobar si el daemon esta corriendo
    try:
        resultado = subprocess.run(
            ["systemctl", "--user", "is-active", "encadenador_inteligente"],
            capture_output=True, text=True, timeout=5,
        )
        activo = resultado.stdout.strip() == "active"
    except (subprocess.SubprocessError, OSError):
        activo = False

    estado = f"{verde}activo{r}" if activo else f"{rojo}inactivo{r}"
    print(f"  Daemon: {estado}")

    # Cargar cadenas
    cadenas = cargar_todas_las_cadenas()
    print(f"  Cadenas configuradas: {b}{len(cadenas)}{r}")

    total_triggers = sum(len(c["pasos"]) for c in cadenas)
    print(f"  Triggers totales: {b}{total_triggers}{r}")

    cfg_ejecucion = config.get("ejecucion", {})
    cooldown = cfg_ejecucion.get("cooldown", 300)
    print(f"  Cooldown: {cooldown}s")
    print()


def cmd_lista(config: dict):
    """Lista todas las cadenas configuradas con sus detalles."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]
    verde = _COLORES["verde"]
    amarillo = _COLORES["amarillo"]

    print(f"\n{b}{cyan}  Encadenador Inteligente — Cadenas{r}")
    print(f"{d}{'=' * 50}{r}\n")

    cadenas = cargar_todas_las_cadenas()

    if not cadenas:
        print(f"  {amarillo}No hay cadenas configuradas{r}")
        print(f"  {d}Anade ficheros .toml en: {RUTA_CADENAS}{r}\n")
        return

    for cadena in cadenas:
        print(f"  {b}{verde}{cadena['nombre']}{r}")
        print(f"  {d}{cadena.get('descripcion', 'Sin descripcion')}{r}")

        for i, paso in enumerate(cadena["pasos"], 1):
            trigger = paso["trigger"]
            patron = paso["patron"]
            ejecutar = paso["ejecutar"]
            delay = paso.get("delay", 0)

            print(f"    {b}Paso {i}:{r} Cuando {cyan}{trigger}{r} dice \"{patron}\"")
            print(f"           -> {ejecutar}")
            if delay > 0:
                print(f"           {d}(delay: {delay}s){r}")

        print()


def cmd_ejecutar(config: dict, nombre: str):
    """Ejecuta manualmente una cadena completa por nombre."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    rojo = _COLORES["rojo"]
    verde = _COLORES["verde"]
    cyan = _COLORES["cyan"]

    cadenas = cargar_todas_las_cadenas()
    cadena = None
    for c in cadenas:
        if c["nombre"] == nombre:
            cadena = c
            break

    if not cadena:
        print(f"\n  {rojo}Cadena '{nombre}' no encontrada{r}")
        print(f"  Usa {b}cadena --lista{r} para ver las disponibles\n")
        return 1

    print(f"\n{b}{cyan}  Ejecutando cadena: {cadena['nombre']}{r}")
    print(f"  {cadena.get('descripcion', '')}\n")

    resultados = ejecutar_cadena_completa(cadena)

    cfg_historial = config.get("historial", {})
    ruta_historial = cfg_historial.get(
        "ruta",
        str(RUTA_LOGS / "encadenador_historial.json"),
    )
    max_entradas = cfg_historial.get("max_entradas", 200)

    registrar_ejecucion(
        ruta_historial,
        nombre,
        "ejecucion manual",
        resultados,
        max_entradas,
    )

    for i, res in enumerate(resultados, 1):
        if res["exito"]:
            icono = f"{verde}OK{r}"
        else:
            icono = f"{rojo}FALLO{r}"
        print(f"  Paso {i}: [{icono}] {res['comando']}")
        if res["error"]:
            print(f"         {rojo}{res['error'][:100]}{r}")

    print()

    exitos = sum(1 for r in resultados if r["exito"])
    total = len(resultados)
    if exitos == total:
        print(f"  {verde}Cadena completada: {exitos}/{total} pasos exitosos{r}\n")
    else:
        print(f"  {rojo}Cadena fallida: {exitos}/{total} pasos exitosos{r}\n")


def cmd_historial(config: dict):
    """Muestra las ultimas ejecuciones de cadenas."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]
    verde = _COLORES["verde"]
    rojo = _COLORES["rojo"]
    amarillo = _COLORES["amarillo"]

    cfg_historial = config.get("historial", {})
    ruta_historial = cfg_historial.get(
        "ruta",
        str(RUTA_LOGS / "encadenador_historial.json"),
    )

    historial = cargar_historial(ruta_historial)

    print(f"\n{b}{cyan}  Encadenador Inteligente — Historial{r}")
    print(f"{d}{'=' * 50}{r}\n")

    if not historial:
        print(f"  {amarillo}Sin ejecuciones registradas{r}\n")
        return

    # Mostrar las ultimas 20
    for entrada in historial[-20:]:
        ts = entrada.get("timestamp", "?")
        # Formatear timestamp si es ISO
        try:
            dt = datetime.fromisoformat(ts)
            ts_fmt = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            ts_fmt = ts

        nombre = entrada.get("cadena", "?")
        exito = entrada.get("exito", False)
        pasos_ok = entrada.get("pasos_exitosos", 0)
        pasos_total = entrada.get("pasos_total", 0)
        trigger = entrada.get("trigger", "")

        if exito:
            icono = f"{verde}OK{r}"
        else:
            icono = f"{rojo}FALLO{r}"

        print(f"  {d}{ts_fmt}{r}  [{icono}] {b}{nombre}{r} ({pasos_ok}/{pasos_total})")
        if trigger:
            print(f"           {d}Trigger: {trigger[:60]}{r}")

    print()
    print(f"{d}  Total: {len(historial)} ejecuciones registradas{r}\n")


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Encadenador Inteligente — Encadenador de Automatizaciones"
    )

    # Argumentos para el wrapper CLI (sin subcomando)
    parser.add_argument(
        "--status", action="store_true",
        help="Muestra el estado del encadenador"
    )
    parser.add_argument(
        "--lista", action="store_true",
        help="Lista todas las cadenas configuradas"
    )
    parser.add_argument(
        "--ejecutar", metavar="NOMBRE",
        help="Ejecuta una cadena manualmente por nombre"
    )
    parser.add_argument(
        "--historial", action="store_true",
        help="Muestra las ultimas ejecuciones"
    )

    args = parser.parse_args()
    config = cargar_config(RUTA_AUTO)

    if args.status:
        cmd_status(config)
    elif args.lista:
        cmd_lista(config)
    elif args.ejecutar:
        rc = cmd_ejecutar(config, args.ejecutar)
        if rc:
            sys.exit(rc)
    elif args.historial:
        cmd_historial(config)
    else:
        # Sin argumentos: lanza el daemon
        daemon(config)


if __name__ == "__main__":
    main()
