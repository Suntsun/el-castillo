#!/usr/bin/env python3
"""
Nombre: El Heraldo
Propósito: feedback de espera cosmético (spinner + narrador medieval) para
           envolver operaciones bloqueantes, y aviso de hitos por notificación.
Parte del ecosistema: pieza reutilizable de UX en comun/, consumida por `arqui`.
Autor: generado por el Agente Arquitecto
Versión: 1.0.0

Reglas duras (por diseño):
- Si la salida NO es un TTY: NO imprime nada, NO lanza hilo, cero latencia.
  El comportamiento del llamante queda byte-idéntico al de no usar el Heraldo.
- El spinner es puramente cosmético y corre en un hilo aparte; el trabajo real
  bloquea de todos modos, así que no añade latencia perceptible.
- NUNCA propaga una excepción del hilo del spinner al llamante: si algo falla,
  se degrada a silencioso y la operación principal jamás se ve afectada.
- Las frases son AMBIENTALES (no afirman pasos reales del motor).
"""

# ── Configuración ────────────────────────────────────────────────
from __future__ import annotations

import os
import sys
import time
import threading
import itertools
from pathlib import Path
from typing import Iterable

# Fichero de tema persistente y variable de entorno que lo sobreescribe.
_DIR_CONFIG = Path.home() / ".config" / "automatizaciones"
_FICHERO_TEMA = _DIR_CONFIG / "tema"
_ENV_TEMA = "ARQUI_TEMA"

# Temas válidos. Cualquier otro valor cae a TEMA_DEFECTO.
TEMA_DEFECTO = "medieval"
TEMAS_VALIDOS = ("medieval", "clasico")

# Cadencia de refresco del spinner (segundos). Cosmético; rango pedido 0.4-0.8.
INTERVALO_SPINNER = 0.5
# Cada cuántos refrescos rota la frase ambiental.
REFRESCOS_POR_FRASE = 6
# Ancho de borrado de línea al limpiar (cubre frase + glifo holgadamente).
ANCHO_BORRADO = 80

# Glifos del spinner por tema.
_GLIFOS = {
    "medieval": "✶✦✧✩✪✫",
    "clasico": "|/-\\",
}

# Frases ambientales rotatorias por tema. Ambiente, NO pasos reales.
_FRASES = {
    "medieval": (
        "El Arquitecto consulta los pergaminos…",
        "Convoca a los consejeros del castillo…",
        "Descifra runas antiguas…",
        "Despliega el mapa del reino…",
        "Interroga a los heraldos…",
        "Pondera el consejo de la torre…",
    ),
    "clasico": ("Pensando...",),
}

# Consejero por defecto para soldadito(): debe existir en consejeros.toml.
# Configurable por env por si se prefiere otro personaje.
CONSEJERO_HITO_DEFECTO = os.environ.get("ARQUI_CONSEJERO_HITO", "heraldo_mensajes")


# ── Resolución del tema ──────────────────────────────────────────
def tema_actual() -> str:
    """Resuelve el tema activo con prioridad: env > fichero > default.

    Lee la variable de entorno ``ARQUI_TEMA``; si no está, lee
    ``~/.config/automatizaciones/tema``; si tampoco, usa ``TEMA_DEFECTO``.
    Cualquier valor no contemplado en ``TEMAS_VALIDOS`` se sanea a
    ``TEMA_DEFECTO``. Nunca lanza excepción.

    Devuelve: uno de ``TEMAS_VALIDOS``.
    """
    valor = os.environ.get(_ENV_TEMA)
    if valor is None:
        try:
            if _FICHERO_TEMA.exists():
                valor = _FICHERO_TEMA.read_text(encoding="utf-8").strip()
        except OSError:
            valor = None

    if valor:
        valor = valor.strip().lower()
    if valor in TEMAS_VALIDOS:
        return valor
    return TEMA_DEFECTO


# ── Spinner / narrador ───────────────────────────────────────────
class _Pensando:
    """Context manager que pinta un spinner + frase rotatoria en un hilo.

    Solo actúa si ``sys.stdout`` es un TTY. Fuera de TTY es un no-op total:
    no imprime, no lanza hilo, no añade latencia. Al salir del ``with`` para
    el hilo y borra la línea de forma limpia (igual que el ``\\r`` + espacios
    del comportamiento previo). Maneja KeyboardInterrupt sin ensuciar el
    terminal y nunca propaga errores del hilo del spinner al llamante.
    """

    def __init__(self, tema: str | None = None):
        self._tema = tema if tema in TEMAS_VALIDOS else (tema or tema_actual())
        if self._tema not in TEMAS_VALIDOS:
            self._tema = tema_actual()
        self._activo = False
        self._evento_parar = threading.Event()
        self._hilo: threading.Thread | None = None

    # -- API de context manager ------------------------------------
    def __enter__(self) -> "_Pensando":
        try:
            if not self._es_tty():
                return self  # no-op total fuera de TTY
            self._activo = True
            self._hilo = threading.Thread(target=self._bucle, daemon=True)
            self._hilo.start()
        except Exception:  # noqa: BLE001 — degradar a silencioso, jamás romper al llamante
            self._activo = False
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Devuelve False: NO suprime excepciones de la operación envuelta.
        # Usa BaseException (no solo Exception) para que KeyboardInterrupt
        # también dispare la limpieza del terminal antes de propagarse.
        try:
            if self._activo:
                self._evento_parar.set()
                if self._hilo is not None:
                    self._hilo.join(timeout=1.0)
                self._limpiar_linea()
        except BaseException:  # noqa: BLE001 — el cierre nunca debe romper al llamante
            pass
        finally:
            self._activo = False
        return False

    # -- Internos --------------------------------------------------
    @staticmethod
    def _es_tty() -> bool:
        try:
            return bool(sys.stdout.isatty())
        except Exception:  # noqa: BLE001 — si dudamos, asumimos no-TTY (silencioso)
            return False

    def _frases(self) -> Iterable[str]:
        return _FRASES.get(self._tema, _FRASES["clasico"])

    def _glifos(self) -> str:
        return _GLIFOS.get(self._tema, _GLIFOS["clasico"])

    def _bucle(self) -> None:
        """Pinta el spinner hasta que se pide parar. Errores -> silencioso."""
        try:
            glifos = itertools.cycle(self._glifos())
            frases = list(self._frases())
            i_refresco = 0
            while not self._evento_parar.is_set():
                glifo = next(glifos)
                frase = frases[(i_refresco // REFRESCOS_POR_FRASE) % len(frases)]
                # \033[2m = dim, \033[0m = reset (coherente con la paleta del REPL)
                texto = f"\r  \033[2m{glifo} {frase}\033[0m"
                sys.stdout.write(texto)
                sys.stdout.flush()
                i_refresco += 1
                # Espera interrumpible: corta de inmediato al pedir parada.
                self._evento_parar.wait(INTERVALO_SPINNER)
        except Exception:  # noqa: BLE001 — el hilo cosmético nunca debe propagar
            pass

    def _limpiar_linea(self) -> None:
        # Solo en TTY (si llegamos aquí ya sabemos que _activo era True, pero
        # añadimos la guarda por si el estado cambia entre hilos).
        try:
            if not self._es_tty():
                return
            # \033[2K = borrar línea completa (ANSI EL2), luego \r posiciona al
            # inicio. El bloque de espacios como fallback legacy se mantiene para
            # terminales que no soporten la secuencia ANSI.
            sys.stdout.write("\r\033[2K\r" + " " * ANCHO_BORRADO + "\r")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def pensando(tema: str | None = None) -> _Pensando:
    """Devuelve un context manager de espera cosmética.

    Uso:
        with heraldo.pensando(tema=tema_actual()):
            respuesta = operacion_bloqueante()

    Parámetros:
        tema: "medieval" | "clasico". Si es None o inválido, se resuelve con
              ``tema_actual()``.

    No altera el valor de la operación envuelta ni añade latencia perceptible.
    Fuera de TTY es un no-op completo.
    """
    return _Pensando(tema)


# ── Hitos por notificación (soldadito) ───────────────────────────
def soldadito(consejero: str | None = None, mensaje: str = "",
              severidad: str = "info", duracion: int = 5000) -> None:
    """Dispara una notificación de hito reutilizando comun.notificador.notificar.

    Pensado para anunciar hitos de operaciones largas con el personaje
    (popup con su imagen). NO duplica el notificador: lo reutiliza.

    Parámetros:
        consejero: clave de consejeros.toml. Si None, usa CONSEJERO_HITO_DEFECTO.
        mensaje: texto del hito.
        severidad: "info" | "exito" | "aviso" | "error" | "critico".
        duracion: milisegundos que dura el popup.

    Nunca lanza: si el notificador falla, se degrada a silencioso.
    """
    try:
        from comun.notificador import notificar
        notificar(consejero or CONSEJERO_HITO_DEFECTO, mensaje, severidad, duracion)
    except Exception:  # noqa: BLE001 — un hito jamás debe romper la operación principal
        pass
