#!/usr/bin/env python3
import csv
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from monitor_red.monitor_red import (
    ping,
    hay_conexion,
    _formato_duracion,
    registrar_corte,
)


class TestPing(TestCase):
    @patch("monitor_red.monitor_red.subprocess.run")
    def test_ping_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(ping("1.1.1.1"))

    @patch("monitor_red.monitor_red.subprocess.run")
    def test_ping_falla(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(ping("1.1.1.1"))


class TestHayConexion(TestCase):
    @patch("monitor_red.monitor_red.ping")
    def test_primer_host_ok(self, mock_ping):
        mock_ping.side_effect = [True]
        self.assertTrue(hay_conexion(["1.1.1.1", "8.8.8.8"]))
        mock_ping.assert_called_once()

    @patch("monitor_red.monitor_red.ping")
    def test_primer_host_falla_segundo_ok(self, mock_ping):
        mock_ping.side_effect = [False, True]
        self.assertTrue(hay_conexion(["1.1.1.1", "8.8.8.8"]))
        self.assertEqual(mock_ping.call_count, 2)

    @patch("monitor_red.monitor_red.ping")
    def test_todos_fallan(self, mock_ping):
        mock_ping.return_value = False
        self.assertFalse(hay_conexion(["1.1.1.1", "8.8.8.8"]))
        self.assertEqual(mock_ping.call_count, 2)


class TestFormatoDuracion(TestCase):
    def test_segundos(self):
        self.assertEqual(_formato_duracion(45), "45s")

    def test_minutos(self):
        self.assertEqual(_formato_duracion(125), "2m 5s")

    def test_horas(self):
        self.assertEqual(_formato_duracion(3725), "1h 2m")


class TestRegistrarCorte(TestCase):
    def test_crea_fichero_con_cabecera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "historial.csv"
            with patch("monitor_red.monitor_red.RUTA_HISTORIAL", ruta):
                inicio = datetime(2026, 5, 26, 10, 0, 0)
                fin = datetime(2026, 5, 26, 10, 5, 30)
                registrar_corte(inicio, fin, 330)

                with open(ruta, encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                self.assertEqual(reader[0], ["inicio", "fin", "duracion_segundos"])
                self.assertEqual(reader[1][0], "2026-05-26 10:00:00")
                self.assertEqual(reader[1][1], "2026-05-26 10:05:30")
                self.assertEqual(reader[1][2], "330")

    def test_append_sin_duplicar_cabecera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "historial.csv"
            with patch("monitor_red.monitor_red.RUTA_HISTORIAL", ruta):
                inicio1 = datetime(2026, 5, 26, 10, 0, 0)
                fin1 = datetime(2026, 5, 26, 10, 1, 0)
                registrar_corte(inicio1, fin1, 60)

                inicio2 = datetime(2026, 5, 26, 12, 0, 0)
                fin2 = datetime(2026, 5, 26, 12, 3, 0)
                registrar_corte(inicio2, fin2, 180)

                with open(ruta, encoding="utf-8") as f:
                    reader = list(csv.reader(f))
                self.assertEqual(len(reader), 3)  # cabecera + 2 cortes


if __name__ == "__main__":
    main()
