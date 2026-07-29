"""Tests para guardador_silencio (Modo Zen)."""

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from guardador_silencio import guardador_silencio as zen


class TestEstado(unittest.TestCase):
    """Tests para lectura/escritura del fichero de estado."""

    def setUp(self):
        self.fichero_original = zen.FICHERO_ESTADO
        self.fichero_test = Path("/tmp/zen_test_estado.json")
        zen.FICHERO_ESTADO = self.fichero_test

    def tearDown(self):
        if self.fichero_test.exists():
            self.fichero_test.unlink()
        zen.FICHERO_ESTADO = self.fichero_original

    def test_leer_estado_sin_fichero(self):
        """Devuelve None cuando no hay fichero de estado."""
        if self.fichero_test.exists():
            self.fichero_test.unlink()
        self.assertIsNone(zen.leer_estado())

    def test_guardar_y_leer_estado(self):
        """Guarda y lee estado correctamente."""
        fin = datetime.now() + timedelta(minutes=25)
        zen.guardar_estado(25, fin)
        estado = zen.leer_estado()
        self.assertIsNotNone(estado)
        self.assertEqual(estado["duracion_min"], 25)
        self.assertIn("inicio", estado)
        self.assertIn("fin", estado)

    def test_leer_estado_json_corrupto(self):
        """Devuelve None si el JSON esta corrupto."""
        self.fichero_test.write_text("esto no es json{{{")
        self.assertIsNone(zen.leer_estado())

    def test_borrar_estado(self):
        """Borra el fichero de estado."""
        self.fichero_test.write_text("{}")
        zen.borrar_estado()
        self.assertFalse(self.fichero_test.exists())


class TestAccionesSistema(unittest.TestCase):
    """Tests para las acciones del sistema (mako, tejedor, notificaciones)."""

    @patch("guardador_silencio.guardador_silencio.subprocess.run")
    def test_silenciar_mako(self, mock_run):
        """Llama a makoctl set-mode do-not-disturb."""
        mock_run.return_value = MagicMock(returncode=0)
        zen.silenciar_mako()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["makoctl", "set-mode", "do-not-disturb"])

    @patch("guardador_silencio.guardador_silencio.subprocess.run")
    def test_restaurar_mako(self, mock_run):
        """Llama a makoctl set-mode default."""
        mock_run.return_value = MagicMock(returncode=0)
        zen.restaurar_mako()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["makoctl", "set-mode", "default"])

    @patch("guardador_silencio.guardador_silencio.subprocess.run")
    def test_pausar_tejedor(self, mock_run):
        """Llama a systemctl --user stop tejedor_entorno.timer."""
        mock_run.return_value = MagicMock(returncode=0)
        zen.pausar_tejedor()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["systemctl", "--user", "stop", "tejedor_entorno.timer"])

    @patch("guardador_silencio.guardador_silencio.subprocess.run")
    def test_reactivar_tejedor(self, mock_run):
        """Llama a systemctl --user start tejedor_entorno.timer."""
        mock_run.return_value = MagicMock(returncode=0)
        zen.reactivar_tejedor()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["systemctl", "--user", "start", "tejedor_entorno.timer"])

    @patch("guardador_silencio.guardador_silencio.subprocess.run")
    def test_silenciar_mako_timeout(self, mock_run):
        """Maneja timeout de makoctl sin explotar."""
        mock_run.side_effect = subprocess.TimeoutExpired("makoctl", 5)
        # No debe lanzar excepcion
        zen.silenciar_mako()


class TestCLI(unittest.TestCase):
    """Tests para el parser de argumentos."""

    def test_parser_sin_args(self):
        """Sin argumentos, accion por defecto es 'on'."""
        parser = zen.construir_parser()
        args = parser.parse_args([])
        self.assertEqual(args.accion, "on")
        self.assertIsNone(args.duracion)

    def test_parser_on(self):
        """Argumento 'on' se parsea correctamente."""
        parser = zen.construir_parser()
        args = parser.parse_args(["on"])
        self.assertEqual(args.accion, "on")

    def test_parser_off(self):
        """Argumento 'off' se parsea correctamente."""
        parser = zen.construir_parser()
        args = parser.parse_args(["off"])
        self.assertEqual(args.accion, "off")

    def test_parser_status(self):
        """Argumento 'status' se parsea correctamente."""
        parser = zen.construir_parser()
        args = parser.parse_args(["status"])
        self.assertEqual(args.accion, "status")

    def test_parser_on_con_duracion(self):
        """'on 45' parsea duracion como entero."""
        parser = zen.construir_parser()
        args = parser.parse_args(["on", "45"])
        self.assertEqual(args.accion, "on")
        self.assertEqual(args.duracion, 45)

    def test_parser_numero_como_accion(self):
        """Un numero directo se interpreta como accion."""
        parser = zen.construir_parser()
        args = parser.parse_args(["30"])
        self.assertEqual(args.accion, "30")


class TestMostrarEstado(unittest.TestCase):
    """Tests para mostrar_estado."""

    @patch("guardador_silencio.guardador_silencio.leer_estado")
    def test_estado_inactivo(self, mock_leer):
        """Muestra inactivo cuando no hay estado."""
        mock_leer.return_value = None
        # No debe lanzar excepcion
        zen.mostrar_estado()

    @patch("guardador_silencio.guardador_silencio.leer_estado")
    def test_estado_activo(self, mock_leer):
        """Muestra datos cuando hay estado activo."""
        fin = (datetime.now() + timedelta(minutes=10)).isoformat()
        mock_leer.return_value = {
            "inicio": datetime.now().isoformat(),
            "fin": fin,
            "duracion_min": 25,
            "pid": 12345,
        }
        # No debe lanzar excepcion
        zen.mostrar_estado()


import subprocess  # noqa: E402 — needed for TimeoutExpired in tests


class TestValidacionDuracion(unittest.TestCase):
    """Tests para validación de duración ≤0 en main() (ERR-004)."""

    def _ejecutar_main(self, argv: list[str]) -> tuple[int, str]:
        """Ejecuta main() con los argv dados; devuelve (codigo_salida, stderr)."""
        import io
        from unittest.mock import patch as _patch
        stderr_buf = io.StringIO()
        codigo = None
        with _patch("sys.argv", ["zen"] + argv):
            with _patch("sys.stderr", stderr_buf):
                with _patch.object(zen, "cargar_config", return_value={"zen": {}, "notificacion": {}}):
                    try:
                        zen.main()
                        codigo = 0
                    except SystemExit as e:
                        codigo = e.code
        return codigo, stderr_buf.getvalue()

    @patch("guardador_silencio.guardador_silencio.activar_zen")
    def test_duracion_cero_posicional_sale_con_error(self, mock_activar):
        """zen 0 debe salir con código ≠0 sin activar."""
        codigo, stderr = self._ejecutar_main(["0"])
        self.assertNotEqual(codigo, 0)
        mock_activar.assert_not_called()

    @patch("guardador_silencio.guardador_silencio.activar_zen")
    def test_duracion_negativa_posicional_sale_con_error(self, mock_activar):
        """zen -999 debe salir con código ≠0 sin activar."""
        codigo, stderr = self._ejecutar_main(["-999"])
        self.assertNotEqual(codigo, 0)
        mock_activar.assert_not_called()

    @patch("guardador_silencio.guardador_silencio.activar_zen")
    def test_duracion_negativa_on_sale_con_error(self, mock_activar):
        """zen on -5 debe salir con código ≠0 sin activar."""
        codigo, stderr = self._ejecutar_main(["on", "-5"])
        self.assertNotEqual(codigo, 0)
        mock_activar.assert_not_called()

    @patch("guardador_silencio.guardador_silencio.activar_zen")
    def test_duracion_valida_funciona(self, mock_activar):
        """zen 25 debe llamar a activar_zen sin error."""
        mock_activar.return_value = None
        codigo, stderr = self._ejecutar_main(["25"])
        self.assertEqual(codigo, 0)
        mock_activar.assert_called_once()
        args_llamada = mock_activar.call_args[0]
        self.assertEqual(args_llamada[0], 25)

    @patch("guardador_silencio.guardador_silencio.activar_zen")
    def test_duracion_uno_es_valida(self, mock_activar):
        """zen 1 es el mínimo válido; debe funcionar."""
        mock_activar.return_value = None
        codigo, stderr = self._ejecutar_main(["1"])
        self.assertEqual(codigo, 0)
        mock_activar.assert_called_once()

    def test_mensaje_error_menciona_positivo(self):
        """El mensaje de error menciona 'positivo'."""
        import io
        from unittest.mock import patch as _patch
        stderr_buf = io.StringIO()
        with _patch("sys.argv", ["zen", "0"]):
            with _patch("sys.stderr", stderr_buf):
                with _patch.object(zen, "cargar_config", return_value={"zen": {}, "notificacion": {}}):
                    try:
                        zen.main()
                    except SystemExit:
                        pass
        self.assertIn("positivo", stderr_buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
