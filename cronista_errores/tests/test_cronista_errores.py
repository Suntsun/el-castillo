#!/usr/bin/env python3
"""Tests para cronista_errores."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cronista_errores import (
    cargar_posiciones,
    guardar_posiciones,
    inicializar_posiciones,
    escanear_logs,
    registrar_en_log_global,
    _resumen_corto,
    _parsear_linea_global,
    _filtrar_por_periodo,
    RE_LINEA_LOG,
)


class TestRegexLineaLog(unittest.TestCase):
    """Tests para la regex de parseo de líneas de log."""

    def test_linea_error(self):
        linea = "2026-05-26 14:01:09,242 | ERROR | actualizador | Error actualizando pacman: error: failed"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "2026-05-26 14:01:09")
        self.assertEqual(m.group(2), "ERROR")
        self.assertEqual(m.group(3), "actualizador")
        self.assertEqual(m.group(4), "Error actualizando pacman: error: failed")

    def test_linea_critical(self):
        linea = "2026-05-26 22:48:26,614 | CRITICAL | guardador_silencio | Fallo grave"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "CRITICAL")

    def test_linea_info_no_captura_error(self):
        linea = "2026-05-26 14:01:09,241 | INFO | actualizador | Todo bien"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "INFO")

    def test_linea_malformada(self):
        linea = "esto no es un log valido"
        m = RE_LINEA_LOG.match(linea)
        self.assertIsNone(m)


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
        resultado = cargar_posiciones("/tmp/no_existe_posiciones_test.json")
        self.assertEqual(resultado, {})

    def test_cargar_json_corrupto(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("esto no es json{{{")
            ruta = f.name

        resultado = cargar_posiciones(ruta)
        self.assertEqual(resultado, {})
        Path(ruta).unlink()


class TestEscanearLogs(unittest.TestCase):
    """Tests para el escaneo de logs."""

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

    @patch("cronista_errores.RUTA_LOGS")
    def test_detecta_error(self, mock_ruta):
        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [
            self._crear_log(
                "actualizador",
                "2026-05-26 14:01:09,242 | ERROR | actualizador | Error de pacman\n"
            )
        ]

        errores, posiciones = escanear_logs({}, ["ERROR", "CRITICAL"])

        self.assertEqual(len(errores), 1)
        self.assertEqual(errores[0]["automatizacion"], "actualizador")
        self.assertEqual(errores[0]["severidad"], "ERROR")
        self.assertIn("Error de pacman", errores[0]["mensaje"])

    @patch("cronista_errores.RUTA_LOGS")
    def test_ignora_info(self, mock_ruta):
        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [
            self._crear_log(
                "actualizador",
                "2026-05-26 14:01:09,241 | INFO | actualizador | Todo bien\n"
            )
        ]

        errores, _ = escanear_logs({}, ["ERROR", "CRITICAL"])
        self.assertEqual(len(errores), 0)

    @patch("cronista_errores.RUTA_LOGS")
    def test_respeta_posicion_anterior(self, mock_ruta):
        log_file = self._crear_log(
            "test_auto",
            "2026-05-26 10:00:00,000 | ERROR | test_auto | Error viejo\n"
            "2026-05-26 11:00:00,000 | ERROR | test_auto | Error nuevo\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        # Posición tras la primera línea
        primera_linea = "2026-05-26 10:00:00,000 | ERROR | test_auto | Error viejo\n"
        pos = len(primera_linea.encode("utf-8"))

        errores, _ = escanear_logs({str(log_file): pos}, ["ERROR", "CRITICAL"])

        self.assertEqual(len(errores), 1)
        self.assertIn("Error nuevo", errores[0]["mensaje"])

    @patch("cronista_errores.RUTA_LOGS")
    def test_detecta_rotacion_log(self, mock_ruta):
        """Si el archivo se redujo (rotación), lee desde el inicio."""
        log_file = self._crear_log(
            "test_auto",
            "2026-05-26 12:00:00,000 | ERROR | test_auto | Post rotacion\n"
        )

        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [log_file]

        # Posición mayor que el tamaño actual -> rotación detectada
        errores, _ = escanear_logs(
            {str(log_file): 999999}, ["ERROR", "CRITICAL"]
        )

        self.assertEqual(len(errores), 1)
        self.assertIn("Post rotacion", errores[0]["mensaje"])

    @patch("cronista_errores.RUTA_LOGS")
    def test_ignora_logs_propios(self, mock_ruta):
        """No debe monitorizar cronista_errores.log ni errores_global.log."""
        mock_ruta.__class__ = Path
        mock_ruta.exists.return_value = True
        mock_ruta.glob.return_value = [
            self._crear_log(
                "cronista_errores",
                "2026-05-26 12:00:00,000 | ERROR | cronista_errores | Error propio\n"
            ),
            self._crear_log(
                "errores_global",
                "2026-05-26 12:00:00,000 | ERROR | algo | Error global\n"
            ),
        ]

        errores, _ = escanear_logs({}, ["ERROR", "CRITICAL"])
        self.assertEqual(len(errores), 0)


class TestRegistrarLogGlobal(unittest.TestCase):
    """Tests para el registro en log centralizado."""

    def test_escribe_errores(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            ruta = f.name

        errores = [
            {
                "timestamp": "2026-05-26 14:01:09",
                "automatizacion": "actualizador",
                "mensaje": "Error de pacman",
            },
            {
                "timestamp": "2026-05-26 22:48:26",
                "automatizacion": "guardador_silencio",
                "mensaje": "Timeout de mako",
            },
        ]

        registrar_en_log_global(errores, ruta)

        contenido = Path(ruta).read_text()
        self.assertIn("actualizador | Error de pacman", contenido)
        self.assertIn("guardador_silencio | Timeout de mako", contenido)

        lineas = contenido.strip().split("\n")
        self.assertEqual(len(lineas), 2)

        Path(ruta).unlink()

    def test_append_no_sobreescribe(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("2026-05-26 10:00:00 | viejo | Error anterior\n")
            ruta = f.name

        registrar_en_log_global(
            [{"timestamp": "2026-05-26 11:00:00", "automatizacion": "nuevo", "mensaje": "Nuevo error"}],
            ruta,
        )

        contenido = Path(ruta).read_text()
        self.assertIn("Error anterior", contenido)
        self.assertIn("Nuevo error", contenido)

        Path(ruta).unlink()


class TestResumenCorto(unittest.TestCase):
    """Tests para el acortamiento de mensajes."""

    def test_mensaje_corto(self):
        self.assertEqual(_resumen_corto("Error simple"), "Error simple")

    def test_mensaje_largo(self):
        largo = "A" * 100
        resultado = _resumen_corto(largo, 60)
        self.assertEqual(len(resultado), 60)
        self.assertTrue(resultado.endswith("..."))


class TestParsearLineaGlobal(unittest.TestCase):
    """Tests para el parseo de líneas del log global."""

    def test_linea_valida(self):
        linea = "2026-05-26 14:01:09 | actualizador | Error de pacman"
        resultado = _parsear_linea_global(linea)
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["automatizacion"], "actualizador")
        self.assertEqual(resultado["mensaje"], "Error de pacman")
        self.assertIsInstance(resultado["timestamp"], datetime)

    def test_linea_invalida(self):
        self.assertIsNone(_parsear_linea_global("basura"))

    def test_linea_con_pipes_en_mensaje(self):
        linea = "2026-05-26 14:01:09 | actualizador | Error: failed | extra info"
        resultado = _parsear_linea_global(linea)
        self.assertIsNotNone(resultado)
        # El split(maxsplit=2) debe mantener todo el mensaje junto
        self.assertIn("failed | extra info", resultado["mensaje"])


class TestFiltrarPorPeriodo(unittest.TestCase):
    """Tests para el filtrado por periodo temporal."""

    def test_filtra_por_fecha(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("2020-01-01 10:00:00 | viejo | Error antiguo\n")
            f.write("2026-05-26 10:00:00 | nuevo | Error reciente\n")
            ruta = Path(f.name)

        desde = datetime(2026, 1, 1)
        entradas = _filtrar_por_periodo(ruta, desde)

        self.assertEqual(len(entradas), 1)
        self.assertEqual(entradas[0]["automatizacion"], "nuevo")

        ruta.unlink()

    def test_sin_filtro_devuelve_todo(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("2020-01-01 10:00:00 | a | Error 1\n")
            f.write("2026-05-26 10:00:00 | b | Error 2\n")
            ruta = Path(f.name)

        entradas = _filtrar_por_periodo(ruta, desde=None)
        self.assertEqual(len(entradas), 2)

        ruta.unlink()

    def test_archivo_inexistente(self):
        entradas = _filtrar_por_periodo(Path("/tmp/no_existe_test.log"))
        self.assertEqual(entradas, [])


class TestNotificarErrores(unittest.TestCase):
    """Tests para las notificaciones de errores."""

    @patch("cronista_errores.notificar")
    def test_notifica_errores(self, mock_notif):
        from cronista_errores import notificar_errores

        errores = [
            {"automatizacion": "actualizador", "mensaje": "Error de pacman"},
            {"automatizacion": "limpiador", "mensaje": "Sin espacio"},
        ]
        notificar_errores(errores, duracion=5000, max_por_ciclo=5)

        self.assertEqual(mock_notif.call_count, 2)
        # Verificar que el primer argumento es el consejero
        mock_notif.assert_any_call(
            "cronista_errores",
            "Fallo en actualizador: Error de pacman",
            "error",
            5000,
        )

    @patch("cronista_errores.notificar")
    def test_respeta_max_por_ciclo(self, mock_notif):
        from cronista_errores import notificar_errores

        errores = [
            {"automatizacion": f"auto_{i}", "mensaje": f"Error {i}"}
            for i in range(10)
        ]
        notificar_errores(errores, duracion=5000, max_por_ciclo=3)

        # 3 errores + 1 mensaje de "y X más"
        self.assertEqual(mock_notif.call_count, 4)


class TestInicializarPosiciones(unittest.TestCase):
    """Tests para la inicialización de posiciones."""

    @patch("cronista_errores.RUTA_LOGS")
    def test_registra_posiciones_actuales(self, mock_ruta):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir)
            log1 = logs_dir / "actualizador.log"
            log1.write_text("contenido de prueba\n")

            mock_ruta.exists.return_value = True
            mock_ruta.glob.return_value = [log1]

            fichero_pos = Path(tmpdir) / "posiciones.json"
            posiciones = inicializar_posiciones(fichero_pos)

            self.assertIn(str(log1), posiciones)
            self.assertEqual(posiciones[str(log1)], log1.stat().st_size)


if __name__ == "__main__":
    unittest.main()
