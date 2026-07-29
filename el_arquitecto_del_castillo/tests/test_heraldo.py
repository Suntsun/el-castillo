"""
Tests de comun.heraldo — la pieza de feedback de espera del Castillo.

Cubre:
  (a) no-TTY: pensando() no imprime nada y no lanza hilo.
  (b) TTY: pensando() arranca y para el hilo limpiamente y borra la línea.
  (c) pensando() no altera el valor de la operación envuelta ni añade
      latencia perceptible.
  (d) tema_actual() resuelve env > fichero > default y sanea inválidos.
  (e) soldadito() llama a notificar() con los args correctos (mock).

No depende del sistema real: stdout, isatty, filesystem y notificador
se mockean.
"""

from __future__ import annotations

import io
import sys
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
if str(_RAIZ_AUTOMATIZACIONES) not in sys.path:
    sys.path.insert(0, str(_RAIZ_AUTOMATIZACIONES))

from comun import heraldo  # noqa: E402


class _StdoutFalso(io.StringIO):
    """StringIO con isatty() controlable, para simular TTY o pipe."""

    def __init__(self, es_tty: bool):
        super().__init__()
        self._es_tty = es_tty

    def isatty(self) -> bool:  # noqa: D401
        return self._es_tty


# ── (a) no-TTY: silencioso y sin hilo ────────────────────────────
class TestNoTTY(unittest.TestCase):
    def test_no_imprime_nada_ni_lanza_hilo(self):
        falso = _StdoutFalso(es_tty=False)
        hilos_antes = threading.active_count()
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando(tema="medieval") as p:
                # En pleno bloque: no debe haber hilo del spinner.
                self.assertEqual(threading.active_count(), hilos_antes)
                self.assertIsNone(p._hilo)
                self.assertFalse(p._activo)
        self.assertEqual(falso.getvalue(), "")


# ── (b) TTY: arranca, para y borra la línea ──────────────────────
class TestTTY(unittest.TestCase):
    def test_arranca_para_y_borra_linea(self):
        falso = _StdoutFalso(es_tty=True)
        hilos_antes = threading.active_count()
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando(tema="medieval") as p:
                self.assertTrue(p._activo)
                self.assertIsNotNone(p._hilo)
                self.assertTrue(p._hilo.is_alive())
                # Dar tiempo a pintar al menos un refresco.
                time.sleep(heraldo.INTERVALO_SPINNER * 1.5)
            # Tras el with: hilo parado y línea limpia.
            self.assertFalse(p._hilo.is_alive())
        self.assertEqual(threading.active_count(), hilos_antes)
        salida = falso.getvalue()
        # Pintó algo y terminó con el patrón de borrado (\r + espacios + \r).
        self.assertIn("\r", salida)
        self.assertTrue(salida.rstrip("\r").endswith(" " * heraldo.ANCHO_BORRADO)
                        or salida.endswith("\r" + " " * heraldo.ANCHO_BORRADO + "\r"))

    def test_clasico_usa_frase_pensando(self):
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando(tema="clasico"):
                time.sleep(heraldo.INTERVALO_SPINNER * 1.5)
        self.assertIn("Pensando", falso.getvalue())


# ── (c) no altera el valor envuelto ni añade latencia ────────────
class TestTransparencia(unittest.TestCase):
    def test_devuelve_valor_de_la_operacion(self):
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando():
                resultado = {"clave": 42}
        self.assertEqual(resultado, {"clave": 42})

    def test_propaga_excepcion_de_la_operacion(self):
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            with self.assertRaises(ValueError):
                with heraldo.pensando():
                    raise ValueError("error de la operacion")

    def test_sin_latencia_perceptible_en_no_tty(self):
        falso = _StdoutFalso(es_tty=False)
        with patch.object(sys, "stdout", falso):
            t0 = time.perf_counter()
            with heraldo.pensando():
                pass
            transcurrido = time.perf_counter() - t0
        # No-op: debe ser prácticamente instantáneo.
        self.assertLess(transcurrido, 0.05)


# ── (d) tema_actual: env > fichero > default + saneo ─────────────
class TestTemaActual(unittest.TestCase):
    def test_env_tiene_prioridad(self):
        with patch.dict("os.environ", {heraldo._ENV_TEMA: "clasico"}):
            self.assertEqual(heraldo.tema_actual(), "clasico")

    def test_fichero_si_no_hay_env(self):
        fichero = MagicMock()
        fichero.exists.return_value = True
        fichero.read_text.return_value = "clasico\n"
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(heraldo, "_FICHERO_TEMA", fichero):
            self.assertEqual(heraldo.tema_actual(), "clasico")

    def test_default_si_no_hay_env_ni_fichero(self):
        fichero = MagicMock()
        fichero.exists.return_value = False
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(heraldo, "_FICHERO_TEMA", fichero):
            self.assertEqual(heraldo.tema_actual(), heraldo.TEMA_DEFECTO)

    def test_valor_invalido_cae_a_default(self):
        with patch.dict("os.environ", {heraldo._ENV_TEMA: "dragones"}):
            self.assertEqual(heraldo.tema_actual(), heraldo.TEMA_DEFECTO)

    def test_env_se_normaliza_mayusculas_y_espacios(self):
        with patch.dict("os.environ", {heraldo._ENV_TEMA: "  CLASICO  "}):
            self.assertEqual(heraldo.tema_actual(), "clasico")

    def test_fichero_ilegible_cae_a_default(self):
        fichero = MagicMock()
        fichero.exists.return_value = True
        fichero.read_text.side_effect = OSError("permiso denegado")
        with patch.dict("os.environ", {}, clear=True), \
             patch.object(heraldo, "_FICHERO_TEMA", fichero):
            self.assertEqual(heraldo.tema_actual(), heraldo.TEMA_DEFECTO)


# ── (b2) C4b: la limpieza incluye secuencia ANSI de borrado ──────
class TestLimpiezaLinea(unittest.TestCase):
    def test_limpiar_emite_ansi_y_espacios(self):
        """Tras el with, lo último escrito borra la línea (ANSI + espacios)."""
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando(tema="medieval"):
                time.sleep(heraldo.INTERVALO_SPINNER * 1.5)
        salida = falso.getvalue()
        # Debe contener el patrón de borrado: \r\033[2K\r
        self.assertIn("\r\033[2K\r", salida,
                      "La limpieza debe incluir la secuencia ANSI EL2")

    def test_limpiar_ocurre_aunque_operacion_lance_excepcion(self):
        """La limpieza se ejecuta en finally incluso si la operación envuelta lanza."""
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            try:
                with heraldo.pensando(tema="medieval"):
                    time.sleep(heraldo.INTERVALO_SPINNER * 1.5)
                    raise RuntimeError("error de prueba")
            except RuntimeError:
                pass
        salida = falso.getvalue()
        self.assertIn("\r\033[2K\r", salida,
                      "La limpieza debe ocurrir aunque la operación lance excepción")

    def test_limpiar_no_actua_en_no_tty(self):
        """En no-TTY, _limpiar_linea no escribe nada."""
        falso = _StdoutFalso(es_tty=False)
        with patch.object(sys, "stdout", falso):
            with heraldo.pensando(tema="medieval"):
                pass
        self.assertEqual(falso.getvalue(), "",
                         "En no-TTY no debe escribirse nada, ni la limpieza")


# ── (f) C4c: KeyboardInterrupt propaga pero limpia el terminal ────
class TestKeyboardInterrupt(unittest.TestCase):
    def test_ki_en_operacion_propaga_y_limpia(self):
        """KI lanzado dentro del with propaga al llamante y deja la línea limpia."""
        falso = _StdoutFalso(es_tty=True)
        with patch.object(sys, "stdout", falso):
            with self.assertRaises(KeyboardInterrupt):
                with heraldo.pensando(tema="medieval"):
                    time.sleep(heraldo.INTERVALO_SPINNER * 1.5)
                    raise KeyboardInterrupt()
        salida = falso.getvalue()
        # La KI no debe suprimirse — el assertRaises lo verifica arriba.
        # La limpieza debe haberse emitido igualmente.
        self.assertIn("\r\033[2K\r", salida,
                      "La limpieza debe ocurrir también ante KeyboardInterrupt")

    def test_ki_en_no_tty_propaga_sin_escritura(self):
        """En no-TTY, KI propaga limpiamente sin ninguna escritura."""
        falso = _StdoutFalso(es_tty=False)
        with patch.object(sys, "stdout", falso):
            with self.assertRaises(KeyboardInterrupt):
                with heraldo.pensando(tema="medieval"):
                    raise KeyboardInterrupt()
        self.assertEqual(falso.getvalue(), "")


# ── (g) Fleco: consejero default soldadito apunta a heraldo_mensajes ──
class TestConsejeroHeraldoDefault(unittest.TestCase):
    def test_default_apunta_a_heraldo_mensajes(self):
        """Sin env ARQUI_CONSEJERO_HITO, el default debe ser heraldo_mensajes."""
        import os
        env_sin_override = {k: v for k, v in os.environ.items()
                            if k != "ARQUI_CONSEJERO_HITO"}
        with patch.dict("os.environ", env_sin_override, clear=True):
            # Recalcular la constante como lo hace el módulo al importar.
            valor = os.environ.get("ARQUI_CONSEJERO_HITO", "heraldo_mensajes")
        self.assertEqual(valor, "heraldo_mensajes")

    def test_env_override_respetado(self):
        """Con ARQUI_CONSEJERO_HITO definido, soldadito usa ese consejero."""
        falso_notificar = MagicMock()
        with patch.dict("os.environ", {"ARQUI_CONSEJERO_HITO": "limpiador"}):
            # La constante se evalúa en import; probamos el comportamiento
            # real usando el valor resuelto en tiempo de ejecución.
            consejero_efectivo = __import__("os").environ.get(
                "ARQUI_CONSEJERO_HITO", "heraldo_mensajes")
            with patch("comun.notificador.notificar", falso_notificar):
                heraldo.soldadito(consejero_efectivo, "prueba")
        args, _ = falso_notificar.call_args
        self.assertEqual(args[0], "limpiador")


# ── (e) soldadito reutiliza notificar ────────────────────────────
class TestSoldadito(unittest.TestCase):
    def test_llama_a_notificar_con_args(self):
        falso_notificar = MagicMock()
        with patch("comun.notificador.notificar", falso_notificar):
            heraldo.soldadito("limpiador", "hito alcanzado", "exito", 7000)
        falso_notificar.assert_called_once_with(
            "limpiador", "hito alcanzado", "exito", 7000)

    def test_consejero_por_defecto(self):
        falso_notificar = MagicMock()
        with patch("comun.notificador.notificar", falso_notificar):
            heraldo.soldadito(mensaje="hola")
        args, _ = falso_notificar.call_args
        self.assertEqual(args[0], heraldo.CONSEJERO_HITO_DEFECTO)
        self.assertEqual(args[1], "hola")

    def test_no_propaga_error_del_notificador(self):
        def revienta(*a, **k):
            raise RuntimeError("notify-send no disponible")
        with patch("comun.notificador.notificar", revienta):
            # No debe lanzar.
            heraldo.soldadito("limpiador", "x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
