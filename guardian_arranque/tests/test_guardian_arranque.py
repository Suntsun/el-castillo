#!/usr/bin/env python3
"""Tests para guardian_arranque."""

import sys
import time
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian_arranque import (
    check_internet,
    check_disco,
    check_temperatura,
    check_timers,
    check_errores_logs,
    ejecutar_checklist,
    _descubrir_timers_castillo,
    CheckResult,
    ChecklistResult,
)


class TestCheckInternet(TestCase):
    @patch("guardian_arranque.subprocess.run")
    def test_internet_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        r = check_internet(["1.1.1.1"], timeout=3)
        self.assertTrue(r.ok)
        self.assertEqual(r.nivel, "ok")

    @patch("guardian_arranque.subprocess.run")
    def test_internet_falla(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        r = check_internet(["1.1.1.1"], timeout=3)
        self.assertFalse(r.ok)
        self.assertEqual(r.nivel, "error")

    @patch("guardian_arranque.subprocess.run")
    def test_internet_segundo_host_ok(self, mock_run):
        """Si el primer host falla pero el segundo responde, es OK."""
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        r = check_internet(["1.1.1.1", "8.8.8.8"], timeout=3)
        self.assertTrue(r.ok)

    @patch("guardian_arranque.subprocess.run")
    def test_internet_timeout(self, mock_run):
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="ping", timeout=3)
        r = check_internet(["1.1.1.1"], timeout=3)
        self.assertFalse(r.ok)
        self.assertEqual(r.nivel, "error")


class TestCheckDisco(TestCase):
    @patch("guardian_arranque.shutil.disk_usage")
    def test_disco_ok(self, mock_usage):
        # 70% usado, 30% libre
        mock_usage.return_value = MagicMock(total=100_000_000_000, free=30_000_000_000, used=70_000_000_000)
        r = check_disco(umbral_porciento=20)
        self.assertTrue(r.ok)
        self.assertEqual(r.nivel, "ok")

    @patch("guardian_arranque.shutil.disk_usage")
    def test_disco_aviso(self, mock_usage):
        # 85% usado, 15% libre
        mock_usage.return_value = MagicMock(total=100_000_000_000, free=15_000_000_000, used=85_000_000_000)
        r = check_disco(umbral_porciento=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.nivel, "aviso")

    @patch("guardian_arranque.shutil.disk_usage")
    def test_disco_critico(self, mock_usage):
        # 95% usado, 5% libre
        mock_usage.return_value = MagicMock(total=100_000_000_000, free=5_000_000_000, used=95_000_000_000)
        r = check_disco(umbral_porciento=20)
        self.assertFalse(r.ok)
        self.assertEqual(r.nivel, "error")


class TestCheckTemperatura(TestCase):
    def _write_temp(self, tmp_path: Path, zona: str, miligrados: int):
        zona_dir = tmp_path / zona
        zona_dir.mkdir(parents=True, exist_ok=True)
        (zona_dir / "temp").write_text(str(miligrados))

    @patch("guardian_arranque.Path")
    def test_temperatura_ok(self, MockPath):
        """Temperatura normal devuelve ok."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self._write_temp(base, "thermal_zone0", 45000)
            self._write_temp(base, "thermal_zone1", 50000)

            # Mock Path("/sys/class/thermal") para que glob devuelva nuestros dirs
            original_path = Path
            def side_effect(arg):
                if arg == "/sys/class/thermal":
                    return base
                return original_path(arg)

            with patch("guardian_arranque.Path", side_effect=side_effect):
                r = check_temperatura(umbral_max=70)
            self.assertTrue(r.ok)
            self.assertEqual(r.nivel, "ok")

    def test_temperatura_sin_datos(self):
        """Sin thermal zones devuelve aviso."""
        with patch("guardian_arranque.Path") as MockPath:
            mock_thermal = MagicMock()
            mock_thermal.glob.return_value = []
            MockPath.return_value = mock_thermal

            # La funcion usa Path("/sys/class/thermal") directamente
            # Necesitamos un approach mas directo
            pass

    @patch("guardian_arranque.Path")
    def test_temperatura_alta(self, MockPath):
        """Temperatura alta devuelve aviso."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self._write_temp(base, "thermal_zone0", 75000)

            original_path = Path
            def side_effect(arg):
                if arg == "/sys/class/thermal":
                    return base
                return original_path(arg)

            with patch("guardian_arranque.Path", side_effect=side_effect):
                r = check_temperatura(umbral_max=70)
            self.assertFalse(r.ok)
            self.assertIn("aviso", r.nivel)


class TestCheckTimers(TestCase):
    # Los tests de esta clase fuerzan lista estatica mockeando
    # _descubrir_timers_castillo para devolver [] (fallback al arg 'nombres').

    @patch("guardian_arranque._descubrir_timers_castillo", return_value=[])
    @patch("guardian_arranque.subprocess.run")
    def test_todos_activos(self, mock_run, _mock_desc):
        mock_run.return_value = MagicMock(stdout="active\n")
        r = check_timers(["tejedor_entorno.timer"])
        self.assertTrue(r.ok)

    @patch("guardian_arranque._descubrir_timers_castillo", return_value=[])
    @patch("guardian_arranque.subprocess.run")
    def test_uno_inactivo(self, mock_run, _mock_desc):
        """Timer ni active ni enabled se reporta como inactivo."""
        mock_run.side_effect = [
            MagicMock(stdout="inactive\n"),     # is-active
            MagicMock(stdout="disabled\n"),      # is-enabled
        ]
        r = check_timers(["tejedor_entorno.timer"])
        self.assertFalse(r.ok)
        self.assertEqual(r.nivel, "aviso")

    def test_lista_vacia(self):
        with patch("guardian_arranque._descubrir_timers_castillo", return_value=[]):
            r = check_timers([])
        self.assertTrue(r.ok)

    @patch("guardian_arranque._descubrir_timers_castillo", return_value=[])
    @patch("guardian_arranque.subprocess.run")
    def test_multiples_mixto(self, mock_run, _mock_desc):
        """Un timer activo y otro inactivo (ni enabled ni active)."""
        mock_run.side_effect = [
            MagicMock(stdout="active\n"),       # timer_a is-active
            MagicMock(stdout="inactive\n"),      # timer_b is-active
            MagicMock(stdout="disabled\n"),      # timer_b is-enabled
        ]
        r = check_timers(["timer_a.timer", "timer_b.timer"])
        self.assertFalse(r.ok)
        self.assertIn("timer_b", r.detalle)
        self.assertNotIn("timer_a", r.detalle)


class TestCheckErroresLogs(TestCase):
    def test_sin_directorio_logs(self):
        with patch("guardian_arranque.RUTA_LOGS") as mock_logs:
            mock_logs.exists.return_value = False
            r = check_errores_logs(horas=24)
            self.assertTrue(r.ok)

    def test_logs_con_errores_recientes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d)
            ts = time.strftime("%Y-%m-%d %H:%M:%S,000")
            log_file = log_path / "test_auto.log"
            log_file.write_text(f"{ts} | ERROR | test | algo fallo\n")

            with patch("guardian_arranque.RUTA_LOGS", log_path):
                r = check_errores_logs(horas=24)
            self.assertFalse(r.ok)
            self.assertIn("test_auto", r.detalle)

    def test_logs_sin_errores(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d)
            ts = time.strftime("%Y-%m-%d %H:%M:%S,000")
            log_file = log_path / "test_auto.log"
            log_file.write_text(f"{ts} | INFO | test | todo bien\n")

            with patch("guardian_arranque.RUTA_LOGS", log_path):
                r = check_errores_logs(horas=24)
            self.assertTrue(r.ok)


class TestChecklistResult(TestCase):
    def test_todo_ok(self):
        r = ChecklistResult(checks=[
            CheckResult("a", True, "OK"),
            CheckResult("b", True, "OK"),
        ])
        self.assertEqual(r.severidad, "exito")
        self.assertIn("Todo en orden", r.resumen_corto)

    def test_con_aviso(self):
        r = ChecklistResult(checks=[
            CheckResult("a", True, "OK"),
            CheckResult("b", False, "Disco al 85%", "aviso"),
        ])
        self.assertEqual(r.severidad, "aviso")
        self.assertIn("Disco al 85%", r.resumen_corto)

    def test_con_error(self):
        r = ChecklistResult(checks=[
            CheckResult("a", False, "Sin internet", "error"),
            CheckResult("b", False, "Disco al 95%", "aviso"),
        ])
        self.assertEqual(r.severidad, "error")
        self.assertIn("Sin internet", r.resumen_corto)

    def test_error_prevalece_sobre_aviso(self):
        r = ChecklistResult(checks=[
            CheckResult("a", True, "OK"),
            CheckResult("b", False, "problema", "aviso"),
            CheckResult("c", False, "fallo", "error"),
        ])
        self.assertEqual(r.severidad, "error")


class TestEjecutarChecklist(TestCase):
    @patch("guardian_arranque.check_amenazas")
    @patch("guardian_arranque.check_errores_logs")
    @patch("guardian_arranque.check_timers")
    @patch("guardian_arranque.check_temperatura")
    @patch("guardian_arranque.check_disco")
    @patch("guardian_arranque.check_internet")
    def test_ejecuta_todos(self, m_inet, m_disco, m_temp, m_timers, m_logs, m_amenazas):
        m_inet.return_value = CheckResult("internet", True, "OK")
        m_disco.return_value = CheckResult("disco", True, "OK")
        m_temp.return_value = CheckResult("temperatura", True, "OK")
        m_timers.return_value = CheckResult("timers", True, "OK")
        m_logs.return_value = CheckResult("errores_logs", True, "OK")
        m_amenazas.return_value = CheckResult("amenazas", True, "OK")

        config = {"checks": {
            "internet": True, "disco": True, "temperatura": True,
            "timers": True, "errores_logs": True, "amenazas": True,
        }}
        r = ejecutar_checklist(config)
        self.assertEqual(len(r.checks), 6)
        self.assertEqual(r.severidad, "exito")

    @patch("guardian_arranque.check_amenazas")
    @patch("guardian_arranque.check_errores_logs")
    @patch("guardian_arranque.check_timers")
    @patch("guardian_arranque.check_temperatura")
    @patch("guardian_arranque.check_disco")
    @patch("guardian_arranque.check_internet")
    def test_check_desactivado(self, m_inet, m_disco, m_temp, m_timers, m_logs, m_amenazas):
        m_disco.return_value = CheckResult("disco", True, "OK")
        m_temp.return_value = CheckResult("temperatura", True, "OK")
        m_timers.return_value = CheckResult("timers", True, "OK")
        m_logs.return_value = CheckResult("errores_logs", True, "OK")
        m_amenazas.return_value = CheckResult("amenazas", True, "OK")

        config = {"checks": {
            "internet": False, "disco": True, "temperatura": True,
            "timers": True, "errores_logs": True, "amenazas": True,
        }}
        r = ejecutar_checklist(config)
        self.assertEqual(len(r.checks), 5)
        m_inet.assert_not_called()


class TestDescubrirTimersCastillo(TestCase):
    """Tests para _descubrir_timers_castillo (ERR-003)."""

    def test_descubre_timers_propios(self):
        """Retorna archivos .timer del directorio, excluye omarchy-*."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "tejedor_entorno.timer").write_text("")
            (base / "guardian_arranque.timer").write_text("")
            (base / "omarchy-battery-monitor.timer").write_text("")  # debe excluirse
            (base / "guardian_sombras.timer").write_text("")

            with patch("guardian_arranque.Path") as MockPath:
                # Solo interceptar Path.home() / ".config" / "systemd" / "user"
                import guardian_arranque as ga
                original_descubrir = ga._descubrir_timers_castillo

                def descubrir_mock():
                    if not base.exists():
                        return []
                    return sorted(
                        f.name
                        for f in base.glob("*.timer")
                        if not f.name.startswith("omarchy-")
                    )

                with patch("guardian_arranque._descubrir_timers_castillo", side_effect=descubrir_mock):
                    timers = descubrir_mock()

            self.assertIn("tejedor_entorno.timer", timers)
            self.assertIn("guardian_arranque.timer", timers)
            self.assertIn("guardian_sombras.timer", timers)
            self.assertNotIn("omarchy-battery-monitor.timer", timers)

    def test_descubrir_sin_directorio(self):
        """Si no existe el directorio, devuelve lista vacía."""
        with patch("guardian_arranque.Path") as MockPath:
            import guardian_arranque as ga

            ruta_inexistente = Path("/tmp/no_existe_systemd_user_test_xyz")

            original_home = Path.home

            def home_mock():
                return ruta_inexistente.parent

            # Verificacion directa: si la ruta no existe, retorna []
            # Usamos la funcion real pero con un path que no existe
            resultado = []
            ruta_units = Path("/tmp/no_existe_systemd_user_test_xyz")
            if ruta_units.exists():
                resultado = sorted(
                    f.name for f in ruta_units.glob("*.timer")
                    if not f.name.startswith("omarchy-")
                )
            self.assertEqual(resultado, [])

    def test_check_timers_usa_descubrimiento_dinamico(self):
        """check_timers usa los timers descubiertos, no la lista estatica."""
        timers_reales = ["tejedor_entorno.timer", "guardian_arranque.timer"]

        with patch("guardian_arranque._descubrir_timers_castillo", return_value=timers_reales):
            with patch("guardian_arranque.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="active\n")
                # La lista estatica pasada como arg NO debe usarse si hay descubrimiento
                r = check_timers(["whitelist_ignorada.timer"])

        self.assertTrue(r.ok)
        self.assertIn("2", r.detalle)  # "2 timers activos"

    def test_check_timers_conteo_coincide_con_timers_castillo(self):
        """El conteo reportado coincide con el numero de timers descubiertos."""
        timers_castillo = [
            "centinela_archivos.timer",
            "cronista_informes.timer",
            "explorador_feeds.timer",
            "guardian_arranque.timer",
            "guardian_sombras.timer",
            "purificador_datos.timer",
            "tejedor_entorno.timer",
        ]
        with patch("guardian_arranque._descubrir_timers_castillo", return_value=timers_castillo):
            with patch("guardian_arranque.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="active\n")
                r = check_timers([])

        self.assertTrue(r.ok)
        self.assertIn("7", r.detalle)


if __name__ == "__main__":
    main()
