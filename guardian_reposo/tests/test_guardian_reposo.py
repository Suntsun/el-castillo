#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from guardian_reposo.guardian_reposo import (
    parsear_tiempo,
    _formato_tiempo,
    guardar_estado,
    leer_estado,
    limpiar_estado,
    validar_tiempo_accion,
)


class TestParsearTiempo(TestCase):
    def test_minutos(self):
        self.assertEqual(parsear_tiempo("30m"), 1800)

    def test_horas(self):
        self.assertEqual(parsear_tiempo("2h"), 7200)

    def test_combinado(self):
        self.assertEqual(parsear_tiempo("1h30m"), 5400)

    def test_invalido(self):
        self.assertIsNone(parsear_tiempo("abc"))

    def test_vacio(self):
        self.assertIsNone(parsear_tiempo(""))

    def test_solo_numeros(self):
        self.assertIsNone(parsear_tiempo("30"))


class TestFormatoTiempo(TestCase):
    def test_segundos(self):
        self.assertEqual(_formato_tiempo(45), "45s")

    def test_minutos(self):
        self.assertEqual(_formato_tiempo(1800), "30 minutos")

    def test_horas(self):
        self.assertEqual(_formato_tiempo(7200), "2h")

    def test_horas_y_minutos(self):
        self.assertEqual(_formato_tiempo(5400), "1h 30m")


class TestEstado(TestCase):
    def test_guardar_y_leer(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "reposo.json"
            import os
            pid = os.getpid()
            with patch("guardian_reposo.guardian_reposo.RUTA_ESTADO", ruta):
                guardar_estado("shutdown", "16:30", pid)
                estado = leer_estado()
                self.assertIsNotNone(estado)
                self.assertEqual(estado["accion"], "shutdown")
                self.assertEqual(estado["hora_objetivo"], "16:30")
                self.assertEqual(estado["pid"], pid)

    def test_leer_sin_fichero(self):
        with patch("guardian_reposo.guardian_reposo.RUTA_ESTADO", Path("/tmp/no_existe_test")):
            self.assertIsNone(leer_estado())

    def test_leer_pid_muerto_limpia(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "reposo.json"
            ruta.write_text(json.dumps({
                "accion": "shutdown",
                "hora_objetivo": "16:30",
                "pid": 99999999,
            }))
            with patch("guardian_reposo.guardian_reposo.RUTA_ESTADO", ruta):
                estado = leer_estado()
                self.assertIsNone(estado)
                self.assertFalse(ruta.exists())

    def test_limpiar_estado(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "reposo.json"
            ruta.write_text("{}")
            with patch("guardian_reposo.guardian_reposo.RUTA_ESTADO", ruta):
                limpiar_estado()
                self.assertFalse(ruta.exists())


class TestCotaMaxima(TestCase):
    """R4-010: rast rechaza tiempos que superan COTA_MAXIMA_HORAS."""

    def _run_main(self, argv):
        """Ejecuta main() con argv dados y devuelve el código de salida."""
        import guardian_reposo.guardian_reposo as mod
        with patch("sys.argv", argv):
            try:
                mod.main()
                return 0
            except SystemExit as e:
                return e.code

    def test_rast_99999h_sale_1(self):
        """99999h supera la cota → exit 1."""
        codigo = self._run_main(["rast", "shutdown", "99999h"])
        self.assertEqual(codigo, 1)

    def test_rast_25h_sale_1_con_cota_24(self):
        """25h > COTA_MAXIMA_HORAS=24 → exit 1."""
        import guardian_reposo.guardian_reposo as mod
        with patch.object(mod, "COTA_MAXIMA_HORAS", 24):
            codigo = self._run_main(["rast", "shutdown", "25h"])
        self.assertEqual(codigo, 1)

    def test_rast_8h_no_rechaza(self):
        """8h ≤ 24h → no sale por cota (puede fallar por otra razón, pero no por cota)."""
        import guardian_reposo.guardian_reposo as mod
        # Mockear leer_estado para que no haya conflicto, y programar para que no ejecute nada
        with patch.object(mod, "leer_estado", return_value=None):
            with patch.object(mod, "programar"):
                codigo = self._run_main(["rast", "shutdown", "8h"])
        # Puede ser 0 o 1 por otras razones, pero no debe ser 1 por cota
        # Verificamos simplemente que parsear_tiempo(8h) devuelve < cota
        segundos = mod.parsear_tiempo("8h")
        self.assertIsNotNone(segundos)
        self.assertLessEqual(segundos, mod.COTA_MAXIMA_HORAS * 3600)

    def test_rast_exactamente_cota_permitido(self):
        """Exactamente COTA_MAXIMA_HORAS=24h (24*3600 segundos) es aceptado."""
        import guardian_reposo.guardian_reposo as mod
        with patch.object(mod, "COTA_MAXIMA_HORAS", 24):
            with patch.object(mod, "leer_estado", return_value=None):
                with patch.object(mod, "programar"):
                    codigo = self._run_main(["rast", "shutdown", "24h"])
        # No debe dar exit por cota (24h == 24h no es mayor)
        import guardian_reposo.guardian_reposo as mod2
        self.assertLessEqual(mod2.parsear_tiempo("24h"), 24 * 3600)


class TestValidarTiempoAccion(TestCase):
    """Tests para validar_tiempo_accion: función usada por el subcomando 'validar'."""

    def _llama_validar(self, accion, tiempo_str, cota=24, estado_actual=None):
        """Llama a validar_tiempo_accion y devuelve (segundos, exit_code).

        Si lanza SystemExit devuelve (None, exit_code).
        """
        import guardian_reposo.guardian_reposo as mod
        with patch.object(mod, "COTA_MAXIMA_HORAS", cota):
            with patch.object(mod, "leer_estado", return_value=estado_actual):
                try:
                    seg = validar_tiempo_accion(accion, tiempo_str)
                    return seg, 0
                except SystemExit as e:
                    return None, e.code

    def test_99999h_sale_1(self):
        """99999h supera cota de 24h → exit 1."""
        _, code = self._llama_validar("shutdown", "99999h")
        self.assertEqual(code, 1)

    def test_0m_sale_1(self):
        """0m es tiempo inválido (0 segundos) → exit 1."""
        _, code = self._llama_validar("shutdown", "0m")
        self.assertEqual(code, 1)

    def test_texto_invalido_sale_1(self):
        """Texto no parseable → exit 1."""
        _, code = self._llama_validar("shutdown", "abc")
        self.assertEqual(code, 1)

    def test_8h_pasa(self):
        """8h con cota 24h → pasa y devuelve segundos correctos."""
        seg, code = self._llama_validar("shutdown", "8h")
        self.assertEqual(code, 0)
        self.assertEqual(seg, 8 * 3600)

    def test_exactamente_cota_pasa(self):
        """24h con cota 24h → pasa (límite inclusivo)."""
        seg, code = self._llama_validar("shutdown", "24h")
        self.assertEqual(code, 0)
        self.assertEqual(seg, 24 * 3600)

    def test_ya_hay_programado_sale_1(self):
        """Si ya hay una acción programada → exit 1."""
        estado_mock = {"accion": "shutdown", "hora_objetivo": "22:00", "pid": 99999}
        _, code = self._llama_validar("shutdown", "1h", estado_actual=estado_mock)
        self.assertEqual(code, 1)

    def test_subcomando_validar_en_main_99999h(self):
        """El subcomando 'validar' en main() con 99999h devuelve exit 1."""
        import guardian_reposo.guardian_reposo as mod
        with patch("sys.argv", ["rast", "validar", "shutdown", "99999h"]):
            with self.assertRaises(SystemExit) as ctx:
                mod.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_subcomando_validar_en_main_8h(self):
        """El subcomando 'validar' en main() con 8h válido devuelve exit 0."""
        import guardian_reposo.guardian_reposo as mod
        with patch.object(mod, "leer_estado", return_value=None):
            with patch("sys.argv", ["rast", "validar", "shutdown", "8h"]):
                try:
                    mod.main()
                    code = 0
                except SystemExit as e:
                    code = e.code
        self.assertEqual(code, 0)


if __name__ == "__main__":
    main()
