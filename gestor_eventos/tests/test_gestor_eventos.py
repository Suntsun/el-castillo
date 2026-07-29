#!/usr/bin/env python3
"""Tests para gestor_eventos."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gestor_eventos import (
    evaluar_condicion,
    _parsear_valor,
    cargar_regla,
    cargar_reglas,
    detectar_monitores,
    detectar_red,
    detectar_bateria,
    detectar_usb,
    detectar_carga,
    MotorEventos,
)


class TestParsearValor(unittest.TestCase):
    """Tests para el parseo de valores de condiciones."""

    def test_entero(self):
        self.assertEqual(_parsear_valor("42"), 42)

    def test_float(self):
        self.assertAlmostEqual(_parsear_valor("3.14"), 3.14)

    def test_string_comillas_dobles(self):
        self.assertEqual(_parsear_valor('"down"'), "down")

    def test_string_comillas_simples(self):
        self.assertEqual(_parsear_valor("'up'"), "up")

    def test_booleano_true(self):
        self.assertIs(_parsear_valor("true"), True)

    def test_booleano_false(self):
        self.assertIs(_parsear_valor("false"), False)

    def test_string_sin_comillas(self):
        self.assertEqual(_parsear_valor("algo"), "algo")


class TestEvaluarCondicion(unittest.TestCase):
    """Tests para el evaluador de condiciones."""

    def test_mayor_que(self):
        self.assertTrue(evaluar_condicion("count > 1", {"count": 2}))
        self.assertFalse(evaluar_condicion("count > 1", {"count": 1}))

    def test_menor_que(self):
        self.assertTrue(evaluar_condicion("capacity < 20", {"capacity": 15}))
        self.assertFalse(evaluar_condicion("capacity < 20", {"capacity": 25}))

    def test_igual(self):
        self.assertTrue(evaluar_condicion('state == "down"', {"state": "down"}))
        self.assertFalse(evaluar_condicion('state == "down"', {"state": "up"}))

    def test_distinto(self):
        self.assertTrue(evaluar_condicion('state != "down"', {"state": "up"}))
        self.assertFalse(evaluar_condicion('state != "down"', {"state": "down"}))

    def test_mayor_igual(self):
        self.assertTrue(evaluar_condicion("load1 >= 4.0", {"load1": 4.0}))
        self.assertTrue(evaluar_condicion("load1 >= 4.0", {"load1": 5.0}))
        self.assertFalse(evaluar_condicion("load1 >= 4.0", {"load1": 3.0}))

    def test_menor_igual(self):
        self.assertTrue(evaluar_condicion("capacity <= 20", {"capacity": 20}))
        self.assertTrue(evaluar_condicion("capacity <= 20", {"capacity": 10}))
        self.assertFalse(evaluar_condicion("capacity <= 20", {"capacity": 21}))

    def test_booleano(self):
        self.assertTrue(evaluar_condicion("present == true", {"present": True}))
        self.assertFalse(evaluar_condicion("present == true", {"present": False}))

    def test_variable_inexistente(self):
        self.assertFalse(evaluar_condicion("noexiste > 1", {"count": 2}))

    def test_condicion_malformada(self):
        self.assertFalse(evaluar_condicion("esto no es valido", {"x": 1}))

    def test_espacios_extra(self):
        self.assertTrue(evaluar_condicion("  count  >  1  ", {"count": 2}))


class TestCargarRegla(unittest.TestCase):
    """Tests para la carga de reglas TOML."""

    def test_regla_valida(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write('[regla]\n')
            f.write('nombre = "test"\n')
            f.write('evento = "monitor"\n')
            f.write('condicion = "count > 1"\n')
            f.write('accion = "echo test"\n')
            f.write('cooldown = 10\n')
            ruta = Path(f.name)

        regla = cargar_regla(ruta)
        self.assertIsNotNone(regla)
        self.assertEqual(regla["nombre"], "test")
        self.assertEqual(regla["evento"], "monitor")
        self.assertEqual(regla["condicion"], "count > 1")
        self.assertEqual(regla["accion"], "echo test")
        self.assertEqual(regla["cooldown"], 10)

        ruta.unlink()

    def test_regla_sin_cooldown(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write('[regla]\n')
            f.write('nombre = "test"\n')
            f.write('evento = "monitor"\n')
            f.write('condicion = "count > 1"\n')
            f.write('accion = "echo test"\n')
            ruta = Path(f.name)

        regla = cargar_regla(ruta)
        self.assertIsNotNone(regla)
        self.assertEqual(regla["cooldown"], 0)

        ruta.unlink()

    def test_regla_campo_faltante(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write('[regla]\n')
            f.write('nombre = "test"\n')
            # Falta evento, condicion, accion
            ruta = Path(f.name)

        regla = cargar_regla(ruta)
        self.assertIsNone(regla)

        ruta.unlink()

    def test_regla_fichero_inexistente(self):
        regla = cargar_regla(Path("/tmp/no_existe_regla_test.toml"))
        self.assertIsNone(regla)


class TestCargarReglas(unittest.TestCase):
    """Tests para la carga de multiples reglas."""

    def test_carga_directorio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir)

            (ruta / "a.toml").write_text(
                '[regla]\nnombre = "a"\nevento = "monitor"\n'
                'condicion = "count > 1"\naccion = "echo a"\n'
            )
            (ruta / "b.toml").write_text(
                '[regla]\nnombre = "b"\nevento = "bateria"\n'
                'condicion = "capacity < 20"\naccion = "echo b"\n'
            )

            reglas = cargar_reglas(ruta)
            self.assertEqual(len(reglas), 2)
            nombres = {r["nombre"] for r in reglas}
            self.assertEqual(nombres, {"a", "b"})

    def test_directorio_inexistente(self):
        reglas = cargar_reglas(Path("/tmp/no_existe_dir_reglas_test"))
        self.assertEqual(reglas, [])


class TestDetectarMonitores(unittest.TestCase):
    """Tests para la deteccion de monitores."""

    @patch("gestor_eventos.subprocess.run")
    def test_dos_monitores(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {"name": "eDP-1"},
                {"name": "HDMI-A-1"},
            ]),
        )

        resultado = detectar_monitores()
        self.assertEqual(resultado["count"], 2)
        self.assertIn("eDP-1", resultado["names"])
        self.assertIn("HDMI-A-1", resultado["names"])

    @patch("gestor_eventos.subprocess.run")
    def test_hyprctl_falla(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        resultado = detectar_monitores()
        self.assertEqual(resultado["count"], 0)
        self.assertEqual(resultado["names"], [])

    @patch("gestor_eventos.subprocess.run")
    def test_hyprctl_no_encontrado(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        resultado = detectar_monitores()
        self.assertEqual(resultado["count"], 0)


class TestDetectarBateria(unittest.TestCase):
    """Tests para la deteccion de bateria."""

    @patch("gestor_eventos.Path")
    def test_sin_bateria(self, mock_path_cls):
        # El Path("/sys/class/power_supply") no existe
        ruta_mock = MagicMock()
        ruta_mock.exists.return_value = False

        def side_effect(arg):
            if arg == "/sys/class/power_supply":
                return ruta_mock
            return Path(arg)

        mock_path_cls.side_effect = side_effect

        # Llamar directamente con el path mockeado
        resultado = detectar_bateria()
        # Si /sys/class/power_supply no existe realmente,
        # devolvera present=False (lo cual es correcto para un desktop)
        self.assertIn("present", resultado)
        self.assertIn("capacity", resultado)


class TestDetectarCarga(unittest.TestCase):
    """Tests para la deteccion de carga del sistema."""

    @patch("gestor_eventos.os.getloadavg")
    def test_carga_normal(self, mock_load):
        mock_load.return_value = (1.5, 2.0, 1.8)

        resultado = detectar_carga()
        self.assertEqual(resultado["load1"], 1.5)
        self.assertEqual(resultado["load5"], 2.0)
        self.assertEqual(resultado["load15"], 1.8)

    @patch("gestor_eventos.os.getloadavg")
    def test_carga_error(self, mock_load):
        mock_load.side_effect = OSError("no disponible")

        resultado = detectar_carga()
        self.assertEqual(resultado["load1"], 0.0)


class TestMotorEventos(unittest.TestCase):
    """Tests para el motor de eventos."""

    def setUp(self):
        self.reglas = [
            {
                "nombre": "test_monitor",
                "evento": "monitor",
                "condicion": "count > 1",
                "accion": "echo monitor_test",
                "cooldown": 0,
            },
            {
                "nombre": "test_bateria",
                "evento": "bateria",
                "condicion": "capacity < 20",
                "accion": "echo bateria_test",
                "cooldown": 60,
            },
        ]
        self.config = {
            "daemon": {
                "historial": "/tmp/test_gestor_eventos_historial.json",
                "max_historial": 100,
            }
        }
        # Limpiar historial de tests previos
        historial = Path("/tmp/test_gestor_eventos_historial.json")
        if historial.exists():
            historial.unlink()

    def tearDown(self):
        historial = Path("/tmp/test_gestor_eventos_historial.json")
        if historial.exists():
            historial.unlink()

    @patch("gestor_eventos.subprocess.run")
    def test_regla_se_dispara(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        motor = MotorEventos(self.reglas, self.config)
        resultados = motor.procesar_reglas_para_evento(
            "monitor", {"count": 2, "names": ["eDP-1", "HDMI-A-1"]}
        )

        self.assertEqual(len(resultados), 1)
        self.assertEqual(resultados[0]["regla"], "test_monitor")
        self.assertEqual(resultados[0]["resultado"], "ok")

    def test_regla_no_se_dispara(self):
        motor = MotorEventos(self.reglas, self.config)
        resultados = motor.procesar_reglas_para_evento(
            "monitor", {"count": 1, "names": ["eDP-1"]}
        )

        self.assertEqual(len(resultados), 0)

    @patch("gestor_eventos.subprocess.run")
    def test_cooldown_bloquea_segunda_ejecucion(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        motor = MotorEventos(self.reglas, self.config)

        # Primera ejecucion: se dispara
        resultados1 = motor.procesar_reglas_para_evento(
            "bateria", {"capacity": 15, "status": "Discharging", "present": True}
        )
        self.assertEqual(len(resultados1), 1)

        # Segunda ejecucion inmediata: cooldown bloquea
        resultados2 = motor.procesar_reglas_para_evento(
            "bateria", {"capacity": 15, "status": "Discharging", "present": True}
        )
        self.assertEqual(len(resultados2), 0)

    @patch("gestor_eventos.subprocess.run")
    def test_historial_se_registra(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        motor = MotorEventos(self.reglas, self.config)
        motor.procesar_reglas_para_evento(
            "monitor", {"count": 2, "names": ["eDP-1", "HDMI-A-1"]}
        )

        historial = motor.obtener_historial()
        self.assertEqual(len(historial), 1)
        self.assertEqual(historial[0]["regla"], "test_monitor")
        self.assertEqual(historial[0]["resultado"], "ok")
        self.assertIn("timestamp", historial[0])

    @patch("gestor_eventos.subprocess.run")
    def test_historial_persiste_en_disco(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        motor = MotorEventos(self.reglas, self.config)
        motor.procesar_reglas_para_evento(
            "monitor", {"count": 2, "names": ["eDP-1"]}
        )

        # Verificar que el fichero existe y tiene contenido
        ruta = Path("/tmp/test_gestor_eventos_historial.json")
        self.assertTrue(ruta.exists())

        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)

        self.assertEqual(len(datos), 1)
        self.assertEqual(datos[0]["regla"], "test_monitor")

    @patch("gestor_eventos.subprocess.run")
    def test_historial_maximo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = dict(self.config)
        config["daemon"] = {
            "historial": "/tmp/test_gestor_eventos_historial.json",
            "max_historial": 5,
        }

        reglas = [
            {
                "nombre": "test_sin_cooldown",
                "evento": "monitor",
                "condicion": "count > 0",
                "accion": "echo test",
                "cooldown": 0,
            }
        ]

        motor = MotorEventos(reglas, config)

        for _ in range(10):
            motor.procesar_reglas_para_evento(
                "monitor", {"count": 2, "names": []}
            )

        historial = motor.obtener_historial()
        self.assertLessEqual(len(historial), 5)

    @patch("gestor_eventos.subprocess.run")
    def test_accion_falla(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="command not found"
        )

        motor = MotorEventos(self.reglas, self.config)
        resultados = motor.procesar_reglas_para_evento(
            "monitor", {"count": 2, "names": []}
        )

        self.assertEqual(len(resultados), 1)
        self.assertTrue(resultados[0]["resultado"].startswith("error:"))

    def test_simular_regla_existente(self):
        motor = MotorEventos(self.reglas, self.config)

        with patch("gestor_eventos.detectar_monitores") as mock_det:
            mock_det.return_value = {"count": 2, "names": ["eDP-1"]}
            # Parchear el diccionario DETECTORES
            with patch.dict("gestor_eventos.DETECTORES", {"monitor": mock_det}):
                resultado = motor.simular_regla("test_monitor")

        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["regla"], "test_monitor")
        self.assertTrue(resultado["condicion_cumplida"])

    def test_simular_regla_inexistente(self):
        motor = MotorEventos(self.reglas, self.config)
        resultado = motor.simular_regla("no_existe")
        self.assertIsNone(resultado)


class TestCooldown(unittest.TestCase):
    """Tests especificos para el sistema de cooldown."""

    def test_cooldown_cero_permite_siempre(self):
        reglas = [{
            "nombre": "sin_cooldown",
            "evento": "carga",
            "condicion": "load1 > 0",
            "accion": "true",
            "cooldown": 0,
        }]
        config = {
            "daemon": {
                "historial": "/tmp/test_gestor_cooldown.json",
                "max_historial": 100,
            }
        }

        motor = MotorEventos(reglas, config)

        with patch("gestor_eventos.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            r1 = motor.procesar_reglas_para_evento("carga", {"load1": 1.0, "load5": 1.0, "load15": 1.0})
            r2 = motor.procesar_reglas_para_evento("carga", {"load1": 1.0, "load5": 1.0, "load15": 1.0})

            self.assertEqual(len(r1), 1)
            self.assertEqual(len(r2), 1)

        Path("/tmp/test_gestor_cooldown.json").unlink(missing_ok=True)


# ── R6-006: --test <regla_inexistente> → exit≠0 ──────────────────────────────

class TestR6006CmdTestExit(unittest.TestCase):
    """R6-006: cmd_test con regla inexistente o error de simulacion → exit≠0."""

    def _motor_con_regla_valida(self, tmpdir: str):
        """Devuelve una lista con una regla valida para usar en tests."""
        return [
            {
                "nombre": "regla_real",
                "evento": "monitor",
                "condicion": "count > 5",
                "accion": "echo x",
                "cooldown": 0,
            }
        ]

    def test_regla_inexistente_sale_nonzero(self):
        """--test <nombre_que_no_existe> → sys.exit(1)."""
        from gestor_eventos import cmd_test
        with patch("gestor_eventos.cargar_reglas", return_value=[]):
            with self.assertRaises(SystemExit) as ctx:
                cmd_test({}, "no_existe_esta_regla")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_regla_valida_no_cumplida_exit_0(self):
        """--test <regla_valida> NO cumplida → exit 0 (sin regresion)."""
        reglas = [
            {
                "nombre": "regla_real",
                "evento": "monitor",
                "condicion": "count > 999",
                "accion": "echo x",
                "cooldown": 0,
            }
        ]
        from gestor_eventos import cmd_test
        with patch("gestor_eventos.cargar_reglas", return_value=reglas):
            with patch("gestor_eventos.MotorEventos") as mock_motor_cls:
                mock_motor = MagicMock()
                mock_motor_cls.return_value = mock_motor
                mock_motor.simular_regla.return_value = {
                    "regla": "regla_real",
                    "evento": "monitor",
                    "condicion": "count > 999",
                    "condicion_cumplida": False,
                    "accion": "echo x",
                    "variables": {"count": 1},
                }
                # No debe lanzar SystemExit
                cmd_test({}, "regla_real")

    def test_error_simulacion_sale_nonzero(self):
        """Resultado con clave 'error' → sys.exit(1)."""
        reglas = [
            {
                "nombre": "regla_con_error",
                "evento": "monitor",
                "condicion": "count > 0",
                "accion": "echo x",
                "cooldown": 0,
            }
        ]
        from gestor_eventos import cmd_test
        with patch("gestor_eventos.cargar_reglas", return_value=reglas):
            with patch("gestor_eventos.MotorEventos") as mock_motor_cls:
                mock_motor = MagicMock()
                mock_motor_cls.return_value = mock_motor
                mock_motor.simular_regla.return_value = {
                    "error": "fallo al detectar"
                }
                with self.assertRaises(SystemExit) as ctx:
                    cmd_test({}, "regla_con_error")
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
