#!/usr/bin/env python3
"""Tests para encadenador_inteligente."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from encadenador_inteligente import (
    parsear_cadena_toml,
    cargar_todas_las_cadenas,
    cargar_posiciones,
    guardar_posiciones,
    escanear_completions,
    ejecutar_paso,
    cargar_historial,
    guardar_historial,
    registrar_ejecucion,
    puede_disparar,
    registrar_disparo,
    _ultimo_disparo,
    RE_LINEA_LOG,
)


class TestParsearCadenaToml(unittest.TestCase):
    """Tests para el parseo de cadenas TOML."""

    def test_cadena_completa(self):
        toml = """
[cadena]
nombre = "limpieza_completa"
descripcion = "Despues de limpiar, purificar"

[[pasos]]
trigger = "limpiador"
patron = "Limpieza completada"
ejecutar = "python3 /ruta/purificador.py"
delay = 5
"""
        cadena = parsear_cadena_toml(toml)
        self.assertIsNotNone(cadena)
        self.assertEqual(cadena["nombre"], "limpieza_completa")
        self.assertEqual(cadena["descripcion"], "Despues de limpiar, purificar")
        self.assertEqual(len(cadena["pasos"]), 1)
        self.assertEqual(cadena["pasos"][0]["trigger"], "limpiador")
        self.assertEqual(cadena["pasos"][0]["patron"], "Limpieza completada")
        self.assertEqual(cadena["pasos"][0]["delay"], 5)

    def test_multiples_pasos(self):
        toml = """
[cadena]
nombre = "multi"
descripcion = "Cadena con multiples pasos"

[[pasos]]
trigger = "auto_a"
patron = "Paso A completado"
ejecutar = "echo paso_b"
delay = 0

[[pasos]]
trigger = "auto_b"
patron = "Paso B completado"
ejecutar = "echo paso_c"
delay = 3
"""
        cadena = parsear_cadena_toml(toml)
        self.assertIsNotNone(cadena)
        self.assertEqual(len(cadena["pasos"]), 2)
        self.assertEqual(cadena["pasos"][0]["trigger"], "auto_a")
        self.assertEqual(cadena["pasos"][1]["trigger"], "auto_b")

    def test_cadena_sin_nombre(self):
        toml = """
[cadena]
descripcion = "Sin nombre"

[[pasos]]
trigger = "x"
patron = "algo"
ejecutar = "echo hola"
"""
        cadena = parsear_cadena_toml(toml)
        self.assertIsNone(cadena)

    def test_cadena_sin_pasos(self):
        toml = """
[cadena]
nombre = "vacia"
descripcion = "Sin pasos"
"""
        cadena = parsear_cadena_toml(toml)
        self.assertIsNone(cadena)

    def test_comentarios_ignorados(self):
        toml = """
# Este es un comentario
[cadena]
nombre = "con_comentarios"
# Otro comentario
descripcion = "Tiene comentarios"

[[pasos]]
trigger = "algo"
patron = "patron"
ejecutar = "echo ok"
delay = 0
"""
        cadena = parsear_cadena_toml(toml)
        self.assertIsNotNone(cadena)
        self.assertEqual(cadena["nombre"], "con_comentarios")


class TestCargarTodasLasCadenas(unittest.TestCase):
    """Tests para cargar todas las cadenas de un directorio."""

    def test_carga_directorio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ruta = Path(tmpdir)

            (ruta / "cadena1.toml").write_text("""
[cadena]
nombre = "cadena1"
descripcion = "Primera"

[[pasos]]
trigger = "a"
patron = "patron_a"
ejecutar = "echo 1"
""")
            (ruta / "cadena2.toml").write_text("""
[cadena]
nombre = "cadena2"
descripcion = "Segunda"

[[pasos]]
trigger = "b"
patron = "patron_b"
ejecutar = "echo 2"
""")
            cadenas = cargar_todas_las_cadenas(ruta)
            self.assertEqual(len(cadenas), 2)
            nombres = {c["nombre"] for c in cadenas}
            self.assertIn("cadena1", nombres)
            self.assertIn("cadena2", nombres)

    def test_directorio_vacio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cadenas = cargar_todas_las_cadenas(Path(tmpdir))
            self.assertEqual(len(cadenas), 0)

    def test_directorio_inexistente(self):
        cadenas = cargar_todas_las_cadenas(Path("/tmp/no_existe_encadenador_test"))
        self.assertEqual(len(cadenas), 0)


class TestPosiciones(unittest.TestCase):
    """Tests para carga/guardado de posiciones."""

    def test_guardar_y_cargar(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            ruta = f.name

        posiciones = {"/tmp/test.log": 1234, "/tmp/otro.log": 5678}
        guardar_posiciones(ruta, posiciones)
        cargadas = cargar_posiciones(ruta)

        self.assertEqual(cargadas, posiciones)
        Path(ruta).unlink()

    def test_cargar_inexistente(self):
        resultado = cargar_posiciones("/tmp/no_existe_enc_posiciones_test.json")
        self.assertEqual(resultado, {})

    def test_cargar_json_corrupto(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("esto no es json{{{")
            ruta = f.name

        resultado = cargar_posiciones(ruta)
        self.assertEqual(resultado, {})
        Path(ruta).unlink()


class TestEscanearCompletions(unittest.TestCase):
    """Tests para la deteccion de triggers en los logs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.logs_dir = Path(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _crear_log(self, nombre: str, contenido: str) -> Path:
        ruta = self.logs_dir / f"{nombre}.log"
        ruta.write_text(contenido, encoding="utf-8")
        return ruta

    @patch("encadenador_inteligente.RUTA_LOGS")
    def test_detecta_trigger(self, mock_ruta):
        log_file = self._crear_log(
            "limpiador",
            "2026-05-27 10:00:00,000 | INFO | limpiador | Limpieza completada con exito\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        cadenas = [{
            "nombre": "test_cadena",
            "descripcion": "Test",
            "pasos": [{
                "trigger": "limpiador",
                "patron": "Limpieza completada",
                "ejecutar": "echo ok",
                "delay": 0,
            }],
        }]

        triggers, posiciones = escanear_completions({}, cadenas)

        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["cadena_nombre"], "test_cadena")
        self.assertEqual(triggers[0]["automatizacion"], "limpiador")

    @patch("encadenador_inteligente.RUTA_LOGS")
    def test_ignora_errores(self, mock_ruta):
        """Solo busca en lineas INFO, no en ERROR."""
        log_file = self._crear_log(
            "limpiador",
            "2026-05-27 10:00:00,000 | ERROR | limpiador | Limpieza completada pero con error\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        cadenas = [{
            "nombre": "test_cadena",
            "descripcion": "Test",
            "pasos": [{
                "trigger": "limpiador",
                "patron": "Limpieza completada",
                "ejecutar": "echo ok",
                "delay": 0,
            }],
        }]

        triggers, _ = escanear_completions({}, cadenas)
        self.assertEqual(len(triggers), 0)

    @patch("encadenador_inteligente.RUTA_LOGS")
    def test_respeta_posicion(self, mock_ruta):
        """No re-detecta triggers ya leidos."""
        log_file = self._crear_log(
            "limpiador",
            "2026-05-27 10:00:00,000 | INFO | limpiador | Limpieza completada vieja\n"
            "2026-05-27 11:00:00,000 | INFO | limpiador | Limpieza completada nueva\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        cadenas = [{
            "nombre": "test_cadena",
            "descripcion": "Test",
            "pasos": [{
                "trigger": "limpiador",
                "patron": "Limpieza completada",
                "ejecutar": "echo ok",
                "delay": 0,
            }],
        }]

        primera_linea = "2026-05-27 10:00:00,000 | INFO | limpiador | Limpieza completada vieja\n"
        pos = len(primera_linea.encode("utf-8"))

        triggers, _ = escanear_completions({str(log_file): pos}, cadenas)

        self.assertEqual(len(triggers), 1)
        self.assertIn("nueva", triggers[0]["mensaje"])

    @patch("encadenador_inteligente.RUTA_LOGS")
    def test_no_trigger_sin_patron(self, mock_ruta):
        """No dispara si el mensaje no coincide con el patron."""
        log_file = self._crear_log(
            "limpiador",
            "2026-05-27 10:00:00,000 | INFO | limpiador | Otra cosa diferente\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        cadenas = [{
            "nombre": "test_cadena",
            "descripcion": "Test",
            "pasos": [{
                "trigger": "limpiador",
                "patron": "Limpieza completada",
                "ejecutar": "echo ok",
                "delay": 0,
            }],
        }]

        triggers, _ = escanear_completions({}, cadenas)
        self.assertEqual(len(triggers), 0)

    @patch("encadenador_inteligente.RUTA_LOGS")
    def test_ignora_log_propio(self, mock_ruta):
        """No monitoriza su propio log."""
        log_file = self._crear_log(
            "encadenador_inteligente",
            "2026-05-27 10:00:00,000 | INFO | encadenador_inteligente | Limpieza completada\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        cadenas = [{
            "nombre": "test",
            "descripcion": "Test",
            "pasos": [{
                "trigger": "encadenador_inteligente",
                "patron": "Limpieza completada",
                "ejecutar": "echo ok",
                "delay": 0,
            }],
        }]

        triggers, _ = escanear_completions({}, cadenas)
        self.assertEqual(len(triggers), 0)


class TestEjecutarPaso(unittest.TestCase):
    """Tests para la ejecucion de pasos."""

    @patch("encadenador_inteligente.subprocess.run")
    def test_paso_exitoso(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="OK\n", stderr=""
        )
        paso = {"ejecutar": "echo ok", "delay": 0}
        resultado = ejecutar_paso(paso)

        self.assertTrue(resultado["exito"])
        self.assertEqual(resultado["codigo_salida"], 0)
        mock_run.assert_called_once()

    @patch("encadenador_inteligente.subprocess.run")
    def test_paso_fallido(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error de prueba"
        )
        paso = {"ejecutar": "false", "delay": 0}
        resultado = ejecutar_paso(paso)

        self.assertFalse(resultado["exito"])
        self.assertEqual(resultado["codigo_salida"], 1)

    @patch("encadenador_inteligente.subprocess.run")
    def test_paso_timeout(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="slow", timeout=600)
        paso = {"ejecutar": "sleep 9999", "delay": 0}
        resultado = ejecutar_paso(paso)

        self.assertFalse(resultado["exito"])
        self.assertIn("Timeout", resultado["error"])

    @patch("encadenador_inteligente.time.sleep")
    @patch("encadenador_inteligente.subprocess.run")
    def test_paso_con_delay(self, mock_run, mock_sleep):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        paso = {"ejecutar": "echo hola", "delay": 5}
        resultado = ejecutar_paso(paso)

        mock_sleep.assert_called_once_with(5)
        self.assertTrue(resultado["exito"])


class TestCooldown(unittest.TestCase):
    """Tests para el sistema de cooldown."""

    def setUp(self):
        _ultimo_disparo.clear()

    def test_puede_disparar_primera_vez(self):
        self.assertTrue(puede_disparar("cadena_nueva", 300))

    def test_no_puede_disparar_en_cooldown(self):
        registrar_disparo("cadena_test")
        self.assertFalse(puede_disparar("cadena_test", 300))

    def test_puede_disparar_tras_cooldown(self):
        _ultimo_disparo["cadena_test"] = time.time() - 400
        self.assertTrue(puede_disparar("cadena_test", 300))


class TestHistorial(unittest.TestCase):
    """Tests para el historial de ejecuciones."""

    def test_guardar_y_cargar(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            ruta = f.name

        historial = [
            {"timestamp": "2026-05-27T10:00:00", "cadena": "test", "exito": True},
        ]
        guardar_historial(ruta, historial)
        cargado = cargar_historial(ruta)

        self.assertEqual(len(cargado), 1)
        self.assertEqual(cargado[0]["cadena"], "test")
        Path(ruta).unlink()

    def test_cargar_inexistente(self):
        resultado = cargar_historial("/tmp/no_existe_enc_historial_test.json")
        self.assertEqual(resultado, [])

    def test_truncar_historial(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            ruta = f.name

        historial = [{"i": i} for i in range(300)]
        guardar_historial(ruta, historial, max_entradas=50)
        cargado = cargar_historial(ruta)

        self.assertEqual(len(cargado), 50)
        # Debe conservar los ultimos
        self.assertEqual(cargado[-1]["i"], 299)
        Path(ruta).unlink()

    def test_registrar_ejecucion(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("[]")
            ruta = f.name

        resultados = [
            {"exito": True, "comando": "echo ok"},
            {"exito": True, "comando": "echo fin"},
        ]

        registrar_ejecucion(ruta, "mi_cadena", "limpiador: Limpieza completada", resultados)

        historial = cargar_historial(ruta)
        self.assertEqual(len(historial), 1)
        self.assertEqual(historial[0]["cadena"], "mi_cadena")
        self.assertTrue(historial[0]["exito"])
        self.assertEqual(historial[0]["pasos_total"], 2)
        self.assertEqual(historial[0]["pasos_exitosos"], 2)
        Path(ruta).unlink()


class TestRegexLineaLog(unittest.TestCase):
    """Tests para la regex de parseo de lineas de log."""

    def test_linea_info(self):
        linea = "2026-05-27 10:00:00,123 | INFO | limpiador | Limpieza completada"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "INFO")
        self.assertEqual(m.group(3), "limpiador")
        self.assertEqual(m.group(4), "Limpieza completada")

    def test_linea_error(self):
        linea = "2026-05-27 10:00:00,123 | ERROR | actualizador | Fallo grave"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "ERROR")

    def test_linea_malformada(self):
        m = RE_LINEA_LOG.match("basura sin formato")
        self.assertIsNone(m)


class TestSmokeComandosCadenas(unittest.TestCase):
    """Smoke test: cada paso de cada cadena responde a --help sin bloquearse (ERR-002)."""

    RUTA_CADENAS = Path(__file__).resolve().parent.parent / "cadenas"

    def _extraer_ejecutables(self, toml_path: Path) -> list[str]:
        """Extrae los comandos 'ejecutar' de un TOML de cadena."""
        import re
        texto = toml_path.read_text(encoding="utf-8")
        return re.findall(r'^ejecutar\s*=\s*"([^"]+)"', texto, re.MULTILINE)

    def test_help_limpieza_completa(self):
        """Cada comando de limpieza_completa.toml responde a --help en <5s."""
        self._smoke_cadena(self.RUTA_CADENAS / "limpieza_completa.toml")

    def test_help_post_actualizacion(self):
        """Cada comando de post_actualizacion.toml responde a --help en <5s."""
        self._smoke_cadena(self.RUTA_CADENAS / "post_actualizacion.toml")

    def _smoke_cadena(self, toml_path: Path):
        import subprocess as sp
        self.assertTrue(toml_path.exists(), f"TOML no encontrado: {toml_path}")
        comandos = self._extraer_ejecutables(toml_path)
        self.assertGreater(len(comandos), 0, f"Sin comandos en {toml_path.name}")
        for cmd in comandos:
            partes = cmd.split()
            partes_help = partes + ["--help"]
            try:
                resultado = sp.run(
                    partes_help,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(
                    resultado.returncode, 0,
                    f"--help retornó {resultado.returncode} para: {cmd}\n"
                    f"stderr: {resultado.stderr[:200]}",
                )
            except sp.TimeoutExpired:
                self.fail(f"--help se bloqueó (timeout) para: {cmd}")


class TestCmdEjecutarExitCode(unittest.TestCase):
    """ERR-S01: cmd_ejecutar con cadena inexistente devuelve 1, main propaga exit."""

    def test_cadena_inexistente_devuelve_1(self):
        """cmd_ejecutar retorna 1 cuando la cadena no existe."""
        from encadenador_inteligente import cmd_ejecutar
        with patch("encadenador_inteligente.cargar_todas_las_cadenas", return_value=[]):
            resultado = cmd_ejecutar({}, "cadena_que_no_existe")
        self.assertEqual(resultado, 1)

    def test_cadena_existente_no_devuelve_1(self):
        """cmd_ejecutar NO retorna 1 cuando la cadena existe (ejecución normal)."""
        from encadenador_inteligente import cmd_ejecutar
        cadena_demo = {
            "nombre": "mi_cadena",
            "descripcion": "demo",
            "pasos": [],
        }
        with patch("encadenador_inteligente.cargar_todas_las_cadenas", return_value=[cadena_demo]):
            with patch("encadenador_inteligente.ejecutar_cadena_completa", return_value=[]):
                with patch("encadenador_inteligente.registrar_ejecucion"):
                    resultado = cmd_ejecutar({}, "mi_cadena")
        self.assertNotEqual(resultado, 1)

    def test_main_propaga_exit_1_en_cadena_inexistente(self):
        """main() llama sys.exit(1) cuando cmd_ejecutar devuelve 1."""
        import encadenador_inteligente as mod
        with patch("sys.argv", ["cadena", "--ejecutar", "no_existe"]):
            with patch.object(mod, "cargar_config", return_value={}):
                with patch("encadenador_inteligente.cargar_todas_las_cadenas", return_value=[]):
                    with self.assertRaises(SystemExit) as ctx:
                        mod.main()
        self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
