#!/usr/bin/env python3
"""
Guardián del Arranque — Checklist de Arranque
Comprueba que el sistema esté en orden tras el boot y muestra un semáforo.
Parte del ecosistema: monitorización.
"""

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comun import notificar, configurar_logger, cargar_config, RUTA_LOGS

RUTA_AUTO = Path(__file__).resolve().parent
logger = configurar_logger("guardian_arranque")

CONSEJERO = "guardian_arranque"


# -- Modelo de resultados -----------------------------------------------------

@dataclass
class CheckResult:
    """Resultado de una comprobación individual."""
    nombre: str
    ok: bool
    detalle: str
    nivel: str = "ok"  # "ok", "aviso", "error"


@dataclass
class ChecklistResult:
    """Resultado agregado de todas las comprobaciones."""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def severidad(self) -> str:
        niveles = {c.nivel for c in self.checks}
        if "error" in niveles:
            return "error"
        if "aviso" in niveles:
            return "aviso"
        return "exito"

    @property
    def resumen_corto(self) -> str:
        """Genera resumen breve para la notificación."""
        problemas = [c for c in self.checks if c.nivel != "ok"]
        if not problemas:
            return "Todo en orden, buen dia!"
        partes = [c.detalle for c in problemas]
        return "; ".join(partes)


# -- Comprobaciones individuales -----------------------------------------------

def check_internet(hosts: list[str], timeout: int = 3) -> CheckResult:
    """Comprueba conectividad a internet con ping."""
    for host in hosts:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), host],
                capture_output=True,
                timeout=timeout + 2,
            )
            if result.returncode == 0:
                return CheckResult("internet", True, "Internet OK")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return CheckResult("internet", False, "Sin internet", "error")


def check_disco(umbral_porciento: int = 20) -> CheckResult:
    """Comprueba espacio libre en /."""
    uso = shutil.disk_usage("/")
    porciento_libre = (uso.free / uso.total) * 100
    porciento_usado = 100 - porciento_libre

    if porciento_libre >= umbral_porciento:
        return CheckResult("disco", True, f"Disco OK ({porciento_usado:.0f}% usado)")

    return CheckResult(
        "disco", False,
        f"Disco al {porciento_usado:.0f}%",
        "aviso" if porciento_libre >= 10 else "error",
    )


def check_temperatura(umbral_max: float = 70) -> CheckResult:
    """Lee temperatura de CPU desde /sys/class/thermal."""
    thermal_base = Path("/sys/class/thermal")
    temps: list[float] = []

    for zona in sorted(thermal_base.glob("thermal_zone*")):
        temp_file = zona / "temp"
        try:
            valor = int(temp_file.read_text().strip())
            temps.append(valor / 1000.0)
        except (OSError, ValueError):
            continue

    if not temps:
        return CheckResult("temperatura", False, "Sin datos de temperatura", "aviso")

    max_temp = max(temps)
    if max_temp < umbral_max:
        return CheckResult("temperatura", True, f"CPU a {max_temp:.0f}C")

    return CheckResult(
        "temperatura", False,
        f"CPU a {max_temp:.0f}C (limite: {umbral_max:.0f}C)",
        "aviso" if max_temp < 85 else "error",
    )


def _descubrir_timers_castillo() -> list[str]:
    """Descubre los timers del Castillo en ~/.config/systemd/user/.

    Enumera los archivos *.timer del directorio de unidades del usuario,
    excluyendo las unidades ajenas (omarchy-*) que no pertenecen al ecosistema.
    Devuelve nombres de unidad (p.ej. 'tejedor_entorno.timer').
    """
    ruta_units = Path.home() / ".config" / "systemd" / "user"
    if not ruta_units.exists():
        return []
    return sorted(
        f.name
        for f in ruta_units.glob("*.timer")
        if not f.name.startswith("omarchy-")
    )


def check_timers(nombres: list[str]) -> CheckResult:
    """Comprueba que los timers del Castillo esten activos.

    Usa descubrimiento dinamico desde ~/.config/systemd/user/*.timer
    (excluyendo unidades omarchy-*) en lugar de una whitelist estatica.
    El parametro `nombres` se conserva por compatibilidad pero no se usa
    cuando el descubrimiento dinamico encuentra timers.
    """
    timers = _descubrir_timers_castillo()
    if not timers:
        # Fallback a la lista estatica solo si no hay unidades en disco
        timers = [n for n in nombres if n.endswith(".timer")]

    if not timers:
        return CheckResult("timers", True, "Sin timers configurados")

    inactivos: list[str] = []
    for timer in timers:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", timer],
                capture_output=True, text=True, timeout=5,
            )
            estado = result.stdout.strip()
            if estado not in ("active", "waiting"):
                # Tambien comprobar si esta enabled (cargado pero no disparado aun)
                result_enabled = subprocess.run(
                    ["systemctl", "--user", "is-enabled", timer],
                    capture_output=True, text=True, timeout=5,
                )
                if result_enabled.stdout.strip() != "enabled":
                    inactivos.append(timer)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            inactivos.append(timer)

    if not inactivos:
        return CheckResult("timers", True, f"{len(timers)} timers activos")

    nombres_cortos = [t.replace(".timer", "") for t in inactivos]
    return CheckResult(
        "timers", False,
        f"Timer(s) inactivos: {', '.join(nombres_cortos)}",
        "aviso",
    )


def check_errores_logs(horas: int = 24) -> CheckResult:
    """Busca lineas ERROR/CRITICAL en los logs de automatizaciones de las ultimas N horas."""
    ahora = time.time()
    limite = ahora - (horas * 3600)
    errores_encontrados: list[str] = []

    if not RUTA_LOGS.exists():
        return CheckResult("errores_logs", True, "Sin logs que revisar")

    for log_file in RUTA_LOGS.glob("*.log"):
        try:
            stat = log_file.stat()
            # Si el archivo no se modifico en las ultimas N horas, saltar
            if stat.st_mtime < limite:
                continue

            with open(log_file, encoding="utf-8", errors="replace") as f:
                for linea in f:
                    if "| ERROR |" in linea or "| CRITICAL |" in linea:
                        # Extraer timestamp de la linea para verificar que es reciente
                        try:
                            ts_str = linea.split(" | ")[0].strip()
                            ts = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f"))
                            if ts >= limite:
                                nombre_auto = log_file.stem
                                if nombre_auto not in errores_encontrados:
                                    errores_encontrados.append(nombre_auto)
                        except (ValueError, IndexError):
                            # Si no podemos parsear el timestamp, incluir por seguridad
                            nombre_auto = log_file.stem
                            if nombre_auto not in errores_encontrados:
                                errores_encontrados.append(nombre_auto)
        except OSError:
            continue

    if not errores_encontrados:
        return CheckResult("errores_logs", True, "Sin errores recientes")

    return CheckResult(
        "errores_logs", False,
        f"Errores en: {', '.join(errores_encontrados)}",
        "aviso",
    )


def check_amenazas() -> CheckResult:
    """Ejecuta un escaneo rapido de amenazas con guardian_sombras."""
    try:
        ruta_sombras = Path(__file__).resolve().parent.parent / "guardian_sombras" / "guardian_sombras.py"
        if not ruta_sombras.exists():
            return CheckResult("amenazas", True, "Guardian de sombras no disponible")

        result = subprocess.run(
            ["python3", str(ruta_sombras), "--amenazas"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return CheckResult("amenazas", True, "Sin amenazas detectadas")

        lineas = result.stdout.splitlines()
        for linea in lineas:
            if "criticas" in linea.lower() or "amenaza" in linea.lower():
                texto = linea.strip().replace("\033[1m", "").replace("\033[31m", "").replace("\033[33m", "").replace("\033[0m", "").strip()
                return CheckResult("amenazas", False, texto, "error")

        return CheckResult("amenazas", False, "Amenazas detectadas", "aviso")
    except subprocess.TimeoutExpired:
        return CheckResult("amenazas", False, "Timeout escaneando amenazas", "aviso")
    except Exception as e:
        logger.error(f"Error en check_amenazas: {e}")
        return CheckResult("amenazas", True, "No se pudo escanear amenazas")


# -- Ejecucion del checklist ---------------------------------------------------

def ejecutar_checklist(config: dict) -> ChecklistResult:
    """Ejecuta todas las comprobaciones segun la configuracion."""
    checks_cfg = config.get("checks", {})
    resultado = ChecklistResult()

    if checks_cfg.get("internet", True):
        cfg_inet = config.get("internet", {})
        resultado.checks.append(
            check_internet(
                cfg_inet.get("hosts", ["1.1.1.1", "8.8.8.8"]),
                cfg_inet.get("timeout", 3),
            )
        )

    if checks_cfg.get("disco", True):
        cfg_disco = config.get("disco", {})
        resultado.checks.append(
            check_disco(cfg_disco.get("umbral_porciento_libre", 20))
        )

    if checks_cfg.get("temperatura", True):
        cfg_temp = config.get("temperatura", {})
        resultado.checks.append(
            check_temperatura(cfg_temp.get("umbral_max", 70))
        )

    if checks_cfg.get("timers", True):
        cfg_timers = config.get("timers", {})
        resultado.checks.append(
            check_timers(cfg_timers.get("nombres", []))
        )

    if checks_cfg.get("errores_logs", True):
        cfg_errores = config.get("errores_logs", {})
        resultado.checks.append(
            check_errores_logs(cfg_errores.get("horas", 24))
        )

    if checks_cfg.get("amenazas", True):
        resultado.checks.append(check_amenazas())

    return resultado


# -- Salida terminal con colores ANSI -----------------------------------------

_COLORES = {
    "ok": "\033[32m",      # verde
    "aviso": "\033[33m",   # amarillo
    "error": "\033[31m",   # rojo
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def _icono(nivel: str) -> str:
    return {"ok": "[OK]", "aviso": "[!!]", "error": "[XX]"}.get(nivel, "[??]")


def mostrar_terminal(resultado: ChecklistResult):
    """Muestra el resultado detallado en terminal con colores ANSI."""
    r = _COLORES["reset"]
    b = _COLORES["bold"]
    d = _COLORES["dim"]

    # Cabecera
    sev = resultado.severidad
    color_sev = _COLORES.get(sev, _COLORES.get("ok"))
    titulo_sev = {"exito": "VERDE - Todo OK", "aviso": "AMARILLO - Revisar", "error": "ROJO - Problemas"}.get(sev, sev)
    print(f"\n{b}{color_sev}  Checklist de Arranque: {titulo_sev}{r}")
    print(f"{d}{'=' * 50}{r}\n")

    # Tabla de checks
    for check in resultado.checks:
        color = _COLORES.get(check.nivel, "")
        icono = _icono(check.nivel)
        print(f"  {color}{icono}{r}  {check.detalle}")

    # Pie
    total = len(resultado.checks)
    ok_count = sum(1 for c in resultado.checks if c.ok)
    print(f"\n{d}  {ok_count}/{total} comprobaciones correctas{r}\n")


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Checklist de arranque del sistema"
    )
    parser.add_argument(
        "--silencioso", action="store_true",
        help="Solo mostrar en terminal, no enviar notificacion",
    )
    parser.add_argument(
        "--solo-notificacion", action="store_true",
        help="Solo enviar notificacion, no mostrar en terminal",
    )
    args = parser.parse_args()

    config = cargar_config(RUTA_AUTO)
    logger.info("Iniciando checklist de arranque")

    resultado = ejecutar_checklist(config)

    # Log de cada check
    for check in resultado.checks:
        nivel_log = "info" if check.ok else "warning"
        getattr(logger, nivel_log)(f"{check.nombre}: {check.detalle}")

    logger.info(f"Resultado global: {resultado.severidad} - {resultado.resumen_corto}")

    # Notificacion
    if not args.silencioso:
        cfg_notif = config.get("notificacion", {})
        duracion = cfg_notif.get("duracion", 8000)
        notificar(CONSEJERO, resultado.resumen_corto, resultado.severidad, duracion)

    # Salida terminal
    if not args.solo_notificacion:
        mostrar_terminal(resultado)

    # Codigo de salida segun severidad
    if resultado.severidad == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
