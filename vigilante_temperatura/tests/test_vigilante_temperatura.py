#!/usr/bin/env python3
import sys
import time
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from vigilante_temperatura.vigilante_temperatura import (
    leer_temperaturas,
    evaluar_temperaturas,
    main as vigilante_main,
)

SENSORS_JSON = """{
    "k10temp-pci-00c3": {
        "Adapter": "PCI adapter",
        "Tctl": {"temp1_input": 65.0},
        "Tccd1": {"temp2_input": 63.0}
    },
    "nvme-pci-0100": {
        "Adapter": "PCI adapter",
        "Composite": {"temp1_input": 42.0}
    }
}"""

SENSORS_JSON_HOT = """{
    "k10temp-pci-00c3": {
        "Adapter": "PCI adapter",
        "Tctl": {"temp1_input": 82.0},
        "Tccd1": {"temp2_input": 78.0}
    }
}"""

SENSORS_JSON_CRIT = """{
    "k10temp-pci-00c3": {
        "Adapter": "PCI adapter",
        "Tctl": {"temp1_input": 95.0}
    }
}"""


class TestLeerTemperaturas(TestCase):
    @patch("vigilante_temperatura.vigilante_temperatura.subprocess.run")
    def test_lee_todas(self, mock_run):
        mock_run.return_value = type("R", (), {"returncode": 0, "stdout": SENSORS_JSON, "stderr": ""})()
        temps = leer_temperaturas()
        self.assertIn("k10temp-pci-00c3/Tctl", temps)
        self.assertIn("nvme-pci-0100/Composite", temps)
        self.assertAlmostEqual(temps["k10temp-pci-00c3/Tctl"], 65.0)

    @patch("vigilante_temperatura.vigilante_temperatura.subprocess.run")
    def test_filtra_por_chip(self, mock_run):
        mock_run.return_value = type("R", (), {"returncode": 0, "stdout": SENSORS_JSON, "stderr": ""})()
        temps = leer_temperaturas(["nvme-pci-0100"])
        self.assertNotIn("k10temp-pci-00c3/Tctl", temps)
        self.assertIn("nvme-pci-0100/Composite", temps)

    @patch("vigilante_temperatura.vigilante_temperatura.subprocess.run")
    def test_error_sensors(self, mock_run):
        mock_run.return_value = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        temps = leer_temperaturas()
        self.assertEqual(temps, {})


class TestEvaluarTemperaturas(TestCase):
    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_temperatura_normal_no_alerta(self, mock_notif):
        temps = {"cpu": 50.0}
        cooldowns = {}
        alertas = evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        self.assertEqual(alertas, {})
        mock_notif.assert_not_called()

    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_temperatura_aviso(self, mock_notif):
        temps = {"cpu": 80.0}
        cooldowns = {}
        alertas = evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        self.assertIn("cpu", alertas)
        mock_notif.assert_called_once()
        self.assertIn("aviso", mock_notif.call_args[0][2])

    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_temperatura_critica(self, mock_notif):
        temps = {"cpu": 95.0}
        cooldowns = {}
        alertas = evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        self.assertIn("cpu", alertas)
        mock_notif.assert_called_once()
        self.assertIn("critico", mock_notif.call_args[0][2])

    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_cooldown_previene_spam(self, mock_notif):
        temps = {"cpu": 80.0}
        cooldowns = {"cpu": time.time()}
        alertas = evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        self.assertEqual(alertas, {})
        mock_notif.assert_not_called()

    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_cooldown_expirado_permite_alerta(self, mock_notif):
        temps = {"cpu": 80.0}
        cooldowns = {"cpu": time.time() - 400}
        alertas = evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        self.assertIn("cpu", alertas)
        mock_notif.assert_called_once()

    @patch("vigilante_temperatura.vigilante_temperatura.notificar")
    def test_vuelta_a_normal(self, mock_notif):
        temps = {"cpu": 50.0}
        cooldowns = {"cpu": time.time() - 100}
        evaluar_temperaturas(temps, 75, 90, cooldowns, 300)
        mock_notif.assert_called_once()
        self.assertIn("normalizada", mock_notif.call_args[0][1].lower())
        self.assertNotIn("cpu", cooldowns)


class TestVigilanteOnce(TestCase):
    """N-002: --once hace una lectura y sale; sensors inexistente = error."""

    @patch("vigilante_temperatura.vigilante_temperatura.shutil.which", return_value="/usr/bin/sensors")
    @patch("vigilante_temperatura.vigilante_temperatura.leer_temperaturas")
    @patch("vigilante_temperatura.vigilante_temperatura.cargar_config", return_value={})
    def test_once_imprime_temperaturas(self, _cfg, mock_leer, _which):
        mock_leer.return_value = {"cpu/Tctl": 65.0, "nvme/Composite": 42.0}
        import io
        with patch("sys.argv", ["vigilante_temperatura", "--once"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                vigilante_main()
        salida = mock_stdout.getvalue()
        self.assertIn("65.0", salida)
        mock_leer.assert_called_once()

    @patch("vigilante_temperatura.vigilante_temperatura.shutil.which", return_value="/usr/bin/sensors")
    @patch("vigilante_temperatura.vigilante_temperatura.leer_temperaturas", return_value={})
    @patch("vigilante_temperatura.vigilante_temperatura.cargar_config", return_value={})
    def test_once_sin_temperaturas_sale_1(self, _cfg, _leer, _which):
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["vigilante_temperatura", "--once"]):
                vigilante_main()
        self.assertNotEqual(ctx.exception.code, 0)

    @patch("vigilante_temperatura.vigilante_temperatura.shutil.which", return_value=None)
    def test_sensors_ausente_sale_1(self, _which):
        """Si sensors no está instalado, main() debe salir con exit!=0."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["vigilante_temperatura"]):
                vigilante_main()
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    main()
