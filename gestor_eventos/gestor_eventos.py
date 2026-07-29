#!/usr/bin/env python3
"""
Gestor de Eventos — Bus de Eventos del Sistema
Daemon ligero que escucha cambios del sistema (monitores, red, USB,
bateria, carga) mediante polling y ejecuta acciones configurables
definidas en ficheros TOML.
Parte del ecosistema: monitorizacion.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config

RUTA_AUTO = Path(__file__).resolve().parent
RUTA_REGLAS = RUTA_AUTO / "reglas"

logger = configurar_logger("gestor_eventos")

CONSEJERO = "gestor_eventos"


# -- Evaluador de condiciones --------------------------------------------------

# Regex para parsear expresiones: variable operador valor
# Soporta: count > 1, capacity < 20, state == "down", present == true
RE_CONDICION = re.compile(
    r'^(\w+)\s*(==|!=|>=|<=|>|<)\s*(.+)$'
)


def _parsear_valor(raw: str) -> int | float | str | bool:
    """Convierte un valor crudo de string a su tipo Python."""
    raw = raw.strip()

    # Booleanos
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False

    # Strings entre comillas
    if (raw.startswith('"') and raw.endswith('"')) or \
       (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]

    # Intentar entero
    try:
        return int(raw)
    except ValueError:
        pass

    # Intentar float
    try:
        return float(raw)
    except ValueError:
        pass

    # Si no encaja, devolver como string
    return raw


def evaluar_condicion(condicion: str, variables: dict) -> bool:
    """
    Evalua una expresion simple como 'capacity < 20' o 'state == "down"'.

    Parsea manualmente con regex, NO usa eval().
    Soporta operadores: ==, !=, >, <, >=, <=.
    Las variables se buscan en el diccionario proporcionado.

    Devuelve False si la condicion no se puede parsear o la variable
    no existe.
    """
    match = RE_CONDICION.match(condicion.strip())
    if not match:
        logger.warning(f"Condicion no parseable: {condicion}")
        return False

    nombre_var, operador, valor_raw = match.groups()

    if nombre_var not in variables:
        logger.warning(f"Variable '{nombre_var}' no encontrada en evento")
        return False

    var_actual = variables[nombre_var]
    valor_esperado = _parsear_valor(valor_raw)

    try:
        if operador == "==":
            return var_actual == valor_esperado
        if operador == "!=":
            return var_actual != valor_esperado
        if operador == ">":
            return var_actual > valor_esperado
        if operador == "<":
            return var_actual < valor_esperado
        if operador == ">=":
            return var_actual >= valor_esperado
        if operador == "<=":
            return var_actual <= valor_esperado
    except TypeError:
        logger.warning(
            f"Tipos incomparables: {type(var_actual).__name__} "
            f"{operador} {type(valor_esperado).__name__}"
        )
        return False

    return False


# -- Carga de reglas -----------------------------------------------------------

def cargar_regla(ruta: Path) -> dict | None:
    """
    Carga una regla desde un fichero TOML.

    Devuelve un dict con: nombre, evento, condicion, accion, cooldown.
    Devuelve None si el fichero no se puede leer o le faltan campos.
    """
    try:
        with open(ruta, "rb") as f:
            datos = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Error cargando regla {ruta.name}: {e}")
        return None

    regla = datos.get("regla", {})

    campos_requeridos = ("nombre", "evento", "condicion", "accion")
    for campo in campos_requeridos:
        if campo not in regla:
            logger.error(f"Regla {ruta.name}: falta campo '{campo}'")
            return None

    regla.setdefault("cooldown", 0)
    return regla


def cargar_reglas(ruta_reglas: Path | None = None) -> list[dict]:
    """Carga todas las reglas TOML del directorio de reglas."""
    directorio = ruta_reglas or RUTA_REGLAS
    reglas = []

    if not directorio.exists():
        logger.warning(f"Directorio de reglas no encontrado: {directorio}")
        return reglas

    for fichero in sorted(directorio.glob("*.toml")):
        regla = cargar_regla(fichero)
        if regla:
            reglas.append(regla)

    logger.info(f"Cargadas {len(reglas)} reglas")
    return reglas


# -- Detectores de eventos ----------------------------------------------------

def detectar_monitores() -> dict:
    """
    Detecta monitores conectados via hyprctl monitors -j.

    Devuelve: count (int), names (list[str]).
    """
    try:
        resultado = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True, text=True, timeout=5,
        )
        if resultado.returncode != 0:
            return {"count": 0, "names": []}

        monitores = json.loads(resultado.stdout)
        nombres = [m.get("name", "desconocido") for m in monitores]
        return {"count": len(monitores), "names": nombres}

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning(f"Error detectando monitores: {e}")
        return {"count": 0, "names": []}


def detectar_red() -> list[dict]:
    """
    Detecta el estado de las interfaces de red leyendo /sys/class/net.

    Devuelve una lista de dicts, uno por interfaz, con:
    interface (str), state (str: up/down).
    Excluye la interfaz lo (loopback).
    """
    interfaces = []
    ruta_net = Path("/sys/class/net")

    if not ruta_net.exists():
        return interfaces

    for iface_dir in sorted(ruta_net.iterdir()):
        nombre = iface_dir.name
        if nombre == "lo":
            continue

        operstate = iface_dir / "operstate"
        try:
            estado = operstate.read_text().strip() if operstate.exists() else "unknown"
        except OSError:
            estado = "unknown"

        # Normalizar: up/down/unknown
        if estado not in ("up", "down"):
            estado = "down"

        interfaces.append({"interface": nombre, "state": estado})

    return interfaces


def detectar_bateria() -> dict:
    """
    Lee el estado de la bateria desde /sys/class/power_supply/BAT*.

    Devuelve: capacity (int 0-100), status (str), present (bool).
    Si no hay bateria, present=False.
    """
    ruta_power = Path("/sys/class/power_supply")

    if not ruta_power.exists():
        return {"capacity": 0, "status": "Unknown", "present": False}

    for bat_dir in sorted(ruta_power.glob("BAT*")):
        try:
            capacity_file = bat_dir / "capacity"
            status_file = bat_dir / "status"

            if not capacity_file.exists():
                continue

            capacity = int(capacity_file.read_text().strip())
            status = status_file.read_text().strip() if status_file.exists() else "Unknown"

            return {"capacity": capacity, "status": status, "present": True}
        except (OSError, ValueError) as e:
            logger.warning(f"Error leyendo bateria {bat_dir.name}: {e}")
            continue

    return {"capacity": 0, "status": "Unknown", "present": False}


def detectar_usb() -> dict:
    """
    Lista dispositivos USB leyendo /sys/bus/usb/devices/*/product.

    Devuelve: count (int), devices (list[str] de nombres de producto).
    """
    dispositivos = []
    ruta_usb = Path("/sys/bus/usb/devices")

    if not ruta_usb.exists():
        return {"count": 0, "devices": []}

    for dev_dir in sorted(ruta_usb.iterdir()):
        product_file = dev_dir / "product"
        if product_file.exists():
            try:
                nombre = product_file.read_text().strip()
                if nombre:
                    dispositivos.append(nombre)
            except OSError:
                continue

    return {"count": len(dispositivos), "devices": dispositivos}


def detectar_carga() -> dict:
    """
    Lee la carga del sistema con os.getloadavg().

    Devuelve: load1, load5, load15 (floats).
    """
    try:
        load1, load5, load15 = os.getloadavg()
        return {
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
        }
    except OSError:
        return {"load1": 0.0, "load5": 0.0, "load15": 0.0}


# Mapa de tipo de evento a su funcion detectora
DETECTORES: dict[str, callable] = {
    "monitor": detectar_monitores,
    "bateria": detectar_bateria,
    "carga": detectar_carga,
}

# Detectores que devuelven listas (se evaluan por cada elemento)
DETECTORES_LISTA: dict[str, callable] = {
    "red": detectar_red,
}

# Detectores con tracking de estado previo
DETECTORES_CON_DIFF: dict[str, callable] = {
    "usb": detectar_usb,
}


# -- Motor de reglas -----------------------------------------------------------

class MotorEventos:
    """Motor principal que detecta eventos, evalua reglas y ejecuta acciones."""

    def __init__(self, reglas: list[dict], config: dict):
        self.reglas = reglas
        self.config = config
        self._cooldowns: dict[str, float] = {}
        self._estado_previo: dict[str, dict | list] = {}
        self._historial: list[dict] = []

        cfg_daemon = config.get("daemon", {})
        self._ruta_historial = Path(
            cfg_daemon.get("historial", "/tmp/gestor_eventos_historial.json")
        )
        self._max_historial = cfg_daemon.get("max_historial", 100)

        self._cargar_historial()

    def _cargar_historial(self):
        """Carga el historial existente desde disco."""
        if self._ruta_historial.exists():
            try:
                with open(self._ruta_historial, encoding="utf-8") as f:
                    self._historial = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._historial = []

    def _guardar_historial(self):
        """Persiste el historial en disco."""
        try:
            with open(self._ruta_historial, "w", encoding="utf-8") as f:
                json.dump(self._historial[-self._max_historial:], f, indent=2,
                          ensure_ascii=False)
        except OSError as e:
            logger.error(f"Error guardando historial: {e}")

    def _registrar_evento(self, regla: dict, variables: dict, resultado: str):
        """Registra un evento disparado en el historial."""
        entrada = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "regla": regla["nombre"],
            "evento": regla["evento"],
            "condicion": regla["condicion"],
            "variables": {k: str(v) for k, v in variables.items()},
            "resultado": resultado,
        }
        self._historial.append(entrada)
        # Recortar al maximo
        if len(self._historial) > self._max_historial:
            self._historial = self._historial[-self._max_historial:]
        self._guardar_historial()

    def _verificar_cooldown(self, nombre_regla: str, cooldown: int) -> bool:
        """Devuelve True si la regla puede ejecutarse (cooldown pasado)."""
        if cooldown <= 0:
            return True

        ahora = time.time()
        ultimo = self._cooldowns.get(nombre_regla, 0)

        if ahora - ultimo < cooldown:
            return False

        return True

    def _marcar_cooldown(self, nombre_regla: str):
        """Marca el timestamp actual para el cooldown de una regla."""
        self._cooldowns[nombre_regla] = time.time()

    def ejecutar_accion(self, regla: dict) -> str:
        """
        Ejecuta el comando shell definido en la regla.

        Devuelve 'ok' si el comando termina con returncode 0,
        'error: <detalle>' en caso contrario.
        """
        accion = regla["accion"]
        try:
            resultado = subprocess.run(
                accion, shell=True,
                capture_output=True, text=True, timeout=30,
            )
            if resultado.returncode == 0:
                logger.info(f"Accion ejecutada: {regla['nombre']} -> {accion}")
                return "ok"
            else:
                detalle = resultado.stderr.strip()[:200] if resultado.stderr else f"exit {resultado.returncode}"
                logger.warning(
                    f"Accion fallo: {regla['nombre']} -> {accion}: {detalle}"
                )
                return f"error: {detalle}"

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout ejecutando accion: {regla['nombre']}")
            return "error: timeout"
        except OSError as e:
            logger.error(f"Error ejecutando accion {regla['nombre']}: {e}")
            return f"error: {e}"

    def procesar_reglas_para_evento(
        self, tipo_evento: str, variables: dict
    ) -> list[dict]:
        """
        Evalua todas las reglas del tipo de evento dado contra las variables.

        Ejecuta las acciones de reglas cuya condicion se cumple y cuyo
        cooldown ha pasado. Devuelve lista de resultados.
        """
        resultados = []

        for regla in self.reglas:
            if regla["evento"] != tipo_evento:
                continue

            if not self._verificar_cooldown(regla["nombre"], regla["cooldown"]):
                continue

            if evaluar_condicion(regla["condicion"], variables):
                self._marcar_cooldown(regla["nombre"])
                resultado = self.ejecutar_accion(regla)
                self._registrar_evento(regla, variables, resultado)
                resultados.append({
                    "regla": regla["nombre"],
                    "resultado": resultado,
                })

        return resultados

    def ciclo(self):
        """Ejecuta un ciclo completo de deteccion y evaluacion de reglas."""
        # Detectores simples (devuelven un dict de variables)
        for tipo, detector in DETECTORES.items():
            try:
                variables = detector()
                self.procesar_reglas_para_evento(tipo, variables)
            except Exception as e:
                logger.error(f"Error en detector '{tipo}': {e}")

        # Detectores de lista (evaluan por cada elemento)
        for tipo, detector in DETECTORES_LISTA.items():
            try:
                elementos = detector()
                for variables in elementos:
                    self.procesar_reglas_para_evento(tipo, variables)
            except Exception as e:
                logger.error(f"Error en detector lista '{tipo}': {e}")

        # Detectores con diff (comparan estado previo)
        for tipo, detector in DETECTORES_CON_DIFF.items():
            try:
                estado_actual = detector()
                estado_previo = self._estado_previo.get(tipo, {})

                # Calcular nuevos dispositivos USB
                if tipo == "usb":
                    previos = set(estado_previo.get("devices", []))
                    actuales = set(estado_actual.get("devices", []))
                    nuevos = actuales - previos

                    variables = {
                        "count": estado_actual["count"],
                        "new_devices": list(nuevos),
                    }

                    # Solo evaluar reglas si hay cambio
                    if nuevos or estado_actual["count"] != estado_previo.get("count", 0):
                        self.procesar_reglas_para_evento(tipo, variables)

                self._estado_previo[tipo] = estado_actual

            except Exception as e:
                logger.error(f"Error en detector diff '{tipo}': {e}")

    def obtener_historial(self, n: int = 20) -> list[dict]:
        """Devuelve las ultimas n entradas del historial."""
        return self._historial[-n:]

    def simular_regla(self, nombre_regla: str) -> dict | None:
        """
        Simula una regla: detecta el estado actual y evalua la condicion.

        Devuelve un dict con: regla, variables, condicion_cumplida, accion.
        No ejecuta la accion real.
        """
        for regla in self.reglas:
            if regla["nombre"] == nombre_regla:
                tipo = regla["evento"]

                # Obtener variables actuales
                if tipo in DETECTORES:
                    variables = DETECTORES[tipo]()
                elif tipo in DETECTORES_LISTA:
                    elementos = DETECTORES_LISTA[tipo]()
                    variables = elementos[0] if elementos else {}
                elif tipo in DETECTORES_CON_DIFF:
                    variables = DETECTORES_CON_DIFF[tipo]()
                else:
                    return {
                        "regla": nombre_regla,
                        "error": f"Tipo de evento desconocido: {tipo}",
                    }

                cumplida = evaluar_condicion(regla["condicion"], variables)

                return {
                    "regla": nombre_regla,
                    "evento": tipo,
                    "condicion": regla["condicion"],
                    "variables": {k: str(v) for k, v in variables.items()},
                    "condicion_cumplida": cumplida,
                    "accion": regla["accion"],
                }

        return None


# -- Daemon --------------------------------------------------------------------

_ejecutando = True


def _signal_handler(signum, frame):
    global _ejecutando
    _ejecutando = False
    logger.info("Senal de parada recibida, cerrando...")


def daemon(config: dict):
    """Bucle principal del daemon: polling periodico de eventos."""
    global _ejecutando

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    cfg_daemon = config.get("daemon", {})
    intervalo = cfg_daemon.get("intervalo", 10)

    reglas = cargar_reglas()

    if not reglas:
        logger.warning("No hay reglas configuradas, el daemon no hara nada")

    motor = MotorEventos(reglas, config)

    logger.info(
        f"Daemon iniciado (intervalo={intervalo}s, reglas={len(reglas)})"
    )
    notificar(CONSEJERO, f"Daemon iniciado con {len(reglas)} reglas", "info")

    while _ejecutando:
        try:
            motor.ciclo()
        except Exception as e:
            logger.error(f"Error en ciclo principal: {e}")

        # Dormir en intervalos cortos para responder rapido a senales
        for _ in range(intervalo):
            if not _ejecutando:
                break
            time.sleep(1)

    logger.info("Daemon detenido")
    notificar(CONSEJERO, "Daemon detenido", "info")


# -- CLI -----------------------------------------------------------------------

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
    """Muestra el estado actual de todos los sensores."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]
    verde = _COLORES["verde"]
    rojo = _COLORES["rojo"]
    amarillo = _COLORES["amarillo"]

    print(f"\n{b}{cyan}  Gestor de Eventos — Estado actual{r}")
    print(f"{d}{'=' * 50}{r}\n")

    # Monitores
    mon = detectar_monitores()
    print(f"  {b}Monitores:{r} {mon['count']}")
    for nombre in mon.get("names", []):
        print(f"    {d}- {nombre}{r}")

    # Red
    interfaces = detectar_red()
    print(f"\n  {b}Red:{r}")
    if not interfaces:
        print(f"    {d}Sin interfaces detectadas{r}")
    for iface in interfaces:
        color = verde if iface["state"] == "up" else rojo
        print(f"    {iface['interface']}: {color}{iface['state']}{r}")

    # Bateria
    bat = detectar_bateria()
    if bat["present"]:
        color = verde if bat["capacity"] > 50 else (amarillo if bat["capacity"] > 20 else rojo)
        print(f"\n  {b}Bateria:{r} {color}{bat['capacity']}%{r} ({bat['status']})")
    else:
        print(f"\n  {b}Bateria:{r} {d}No detectada{r}")

    # USB
    usb = detectar_usb()
    print(f"\n  {b}USB:{r} {usb['count']} dispositivo(s)")
    for dev in usb.get("devices", [])[:10]:
        print(f"    {d}- {dev}{r}")

    # Carga
    carga = detectar_carga()
    print(f"\n  {b}Carga:{r} {carga['load1']} / {carga['load5']} / {carga['load15']}")

    print()


def cmd_reglas():
    """Lista todas las reglas configuradas."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]

    reglas = cargar_reglas()

    print(f"\n{b}{cyan}  Gestor de Eventos — Reglas activas{r}")
    print(f"{d}{'=' * 50}{r}\n")

    if not reglas:
        print(f"  {d}No hay reglas configuradas{r}\n")
        return

    for regla in reglas:
        print(f"  {b}{regla['nombre']}{r}")
        print(f"    Evento:    {cyan}{regla['evento']}{r}")
        print(f"    Condicion: {regla['condicion']}")
        print(f"    Accion:    {d}{regla['accion']}{r}")
        print(f"    Cooldown:  {regla['cooldown']}s")
        print()


def cmd_historial(config: dict):
    """Muestra los ultimos eventos disparados."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]
    verde = _COLORES["verde"]
    rojo = _COLORES["rojo"]

    cfg_daemon = config.get("daemon", {})
    ruta_historial = Path(
        cfg_daemon.get("historial", "/tmp/gestor_eventos_historial.json")
    )

    print(f"\n{b}{cyan}  Gestor de Eventos — Historial{r}")
    print(f"{d}{'=' * 50}{r}\n")

    if not ruta_historial.exists():
        print(f"  {d}Sin historial registrado{r}\n")
        return

    try:
        with open(ruta_historial, encoding="utf-8") as f:
            historial = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"  {d}Error leyendo historial{r}\n")
        return

    ultimos = historial[-20:]

    if not ultimos:
        print(f"  {d}Sin eventos registrados{r}\n")
        return

    for entrada in reversed(ultimos):
        color_resultado = verde if entrada.get("resultado") == "ok" else rojo
        print(
            f"  {d}{entrada.get('timestamp', '?')}{r}  "
            f"{b}{entrada.get('regla', '?')}{r}  "
            f"{color_resultado}{entrada.get('resultado', '?')}{r}"
        )

    print(f"\n{d}  Total en historial: {len(historial)} eventos{r}\n")


def cmd_test(config: dict, nombre_regla: str):
    """Simula un evento para probar una regla sin ejecutar la accion."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]
    cyan = _COLORES["cyan"]
    verde = _COLORES["verde"]
    rojo = _COLORES["rojo"]

    reglas = cargar_reglas()
    motor = MotorEventos(reglas, config)
    resultado = motor.simular_regla(nombre_regla)

    print(f"\n{b}{cyan}  Gestor de Eventos — Test de regla{r}")
    print(f"{d}{'=' * 50}{r}\n")

    if resultado is None:
        print(f"  {rojo}Regla '{nombre_regla}' no encontrada{r}")
        print(f"  {d}Reglas disponibles:{r}")
        for regla in reglas:
            print(f"    - {regla['nombre']}")
        print()
        sys.exit(1)

    if "error" in resultado:
        print(f"  {rojo}{resultado['error']}{r}\n")
        sys.exit(1)

    cumplida = resultado["condicion_cumplida"]
    color = verde if cumplida else rojo
    estado = "CUMPLIDA" if cumplida else "NO CUMPLIDA"

    print(f"  {b}Regla:{r}     {resultado['regla']}")
    print(f"  {b}Evento:{r}    {cyan}{resultado['evento']}{r}")
    print(f"  {b}Condicion:{r} {resultado['condicion']}")
    print(f"  {b}Estado:{r}    {color}{estado}{r}")
    print(f"  {b}Accion:{r}    {d}{resultado['accion']}{r}")
    print(f"\n  {b}Variables actuales:{r}")
    for k, v in resultado["variables"].items():
        print(f"    {k} = {v}")

    if cumplida:
        print(f"\n  {d}(La accion NO se ha ejecutado, solo es un test){r}")

    print()


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gestor de Eventos — Bus de Eventos del Sistema"
    )

    parser.add_argument(
        "--status", action="store_true",
        help="Muestra estado actual del sistema (monitores, red, bateria, etc.)"
    )
    parser.add_argument(
        "--reglas", action="store_true",
        help="Lista las reglas activas"
    )
    parser.add_argument(
        "--historial", action="store_true",
        help="Muestra los ultimos 20 eventos disparados"
    )
    parser.add_argument(
        "--test", metavar="REGLA",
        help="Simula un evento para probar una regla"
    )

    args = parser.parse_args()
    config = cargar_config(RUTA_AUTO)

    if args.status:
        cmd_status(config)
    elif args.reglas:
        cmd_reglas()
    elif args.historial:
        cmd_historial(config)
    elif args.test:
        cmd_test(config, args.test)
    else:
        # Sin argumentos: lanzar daemon en primer plano
        daemon(config)


if __name__ == "__main__":
    main()
