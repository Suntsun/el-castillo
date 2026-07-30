#!/usr/bin/env python3
"""Tests para jefe_de_maquinas."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jefe_de_maquinas import (
    descubrir_automatizaciones,
    _buscar_wrapper,
    _ultimo_log,
    _hace_cuanto,
    detectar_servicios,
    _detectar_timers,
    _detectar_daemons,
    _extraer_detalle_timer,
    errores_recientes_24h,
    cmd_logs,
    cmd_resumen,
    _EXCLUIDOS,
)


class TestDescubrirAutomatizaciones(unittest.TestCase):
    """Tests para el descubrimiento de automatizaciones."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta_eco = Path(self.tmpdir)

        # Automatizacion implementada con config
        auto1 = self.ruta_eco / "limpiador"
        auto1.mkdir()
        (auto1 / "limpiador.py").write_text("# script")
        (auto1 / "config.toml").write_text(
            '[general]\nnombre = "limpiador"\ndescripcion = "Limpia el sistema"\n'
        )
        (auto1 / "idea.txt").write_text("idea")

        # Automatizacion solo idea
        auto2 = self.ruta_eco / "forjador_ideas"
        auto2.mkdir()
        (auto2 / "idea.txt").write_text("idea del forjador")

        # Directorio excluido
        comun = self.ruta_eco / "comun"
        comun.mkdir()
        (comun / "__init__.py").write_text("# comun")

        # Fichero suelto (no directorio)
        (self.ruta_eco / "CLAUDE.md").write_text("# doc")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas._buscar_wrapper", return_value=None)
    @patch("jefe_de_maquinas._ultimo_log", return_value=None)
    def test_descubre_implementada(self, mock_log, mock_wrapper, mock_ruta):
        mock_ruta.__class__ = Path
        # Hacer que mock_ruta actue como el Path del tmpdir
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(self.ruta_eco.iterdir())

        autos = descubrir_automatizaciones()

        nombres = [a["nombre"] for a in autos]
        self.assertIn("limpiador", nombres)

        limpiador = next(a for a in autos if a["nombre"] == "limpiador")
        self.assertTrue(limpiador["implementada"])
        self.assertEqual(limpiador["descripcion"], "Limpia el sistema")

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas._buscar_wrapper", return_value=None)
    @patch("jefe_de_maquinas._ultimo_log", return_value=None)
    def test_descubre_solo_idea(self, mock_log, mock_wrapper, mock_ruta):
        mock_ruta.__class__ = Path
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(self.ruta_eco.iterdir())

        autos = descubrir_automatizaciones()

        forjador = next((a for a in autos if a["nombre"] == "forjador_ideas"), None)
        self.assertIsNotNone(forjador)
        self.assertFalse(forjador["implementada"])

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas._buscar_wrapper", return_value=None)
    @patch("jefe_de_maquinas._ultimo_log", return_value=None)
    def test_excluye_comun(self, mock_log, mock_wrapper, mock_ruta):
        mock_ruta.__class__ = Path
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(self.ruta_eco.iterdir())

        autos = descubrir_automatizaciones()

        nombres = [a["nombre"] for a in autos]
        self.assertNotIn("comun", nombres)

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas._buscar_wrapper", return_value=None)
    @patch("jefe_de_maquinas._ultimo_log", return_value=None)
    def test_ignora_ficheros_sueltos(self, mock_log, mock_wrapper, mock_ruta):
        mock_ruta.__class__ = Path
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(self.ruta_eco.iterdir())

        autos = descubrir_automatizaciones()

        nombres = [a["nombre"] for a in autos]
        self.assertNotIn("CLAUDE.md", nombres)


class TestBuscarWrapper(unittest.TestCase):
    """Tests para la busqueda de wrappers CLI."""

    def test_encuentra_wrapper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = Path(tmpdir)
            wrapper = bin_dir / "limpiador"
            wrapper.write_text(
                "#!/bin/bash\nexec python3 /home/usuario/Escritorio/automatizaciones/limpiador/limpiador.py\n"
            )

            with patch("jefe_de_maquinas.RUTA_BIN", bin_dir):
                resultado = _buscar_wrapper("limpiador")
                self.assertEqual(resultado, "limpiador")

    def test_no_encuentra_wrapper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = Path(tmpdir)
            with patch("jefe_de_maquinas.RUTA_BIN", bin_dir):
                resultado = _buscar_wrapper("inexistente")
                self.assertIsNone(resultado)


class TestUltimoLog(unittest.TestCase):
    """Tests para la lectura del ultimo log."""

    def test_lee_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir)
            log = logs_dir / "limpiador.log"
            log.write_text(
                "2026-05-26 10:00:00,123 | INFO | limpiador | Inicio\n"
                "2026-05-27 14:30:00,456 | INFO | limpiador | Fin\n"
            )

            with patch("jefe_de_maquinas.RUTA_LOGS", logs_dir):
                resultado = _ultimo_log("limpiador")
                self.assertIsNotNone(resultado)
                self.assertEqual(resultado.hour, 14)
                self.assertEqual(resultado.minute, 30)

    def test_log_inexistente(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("jefe_de_maquinas.RUTA_LOGS", Path(tmpdir)):
                resultado = _ultimo_log("inexistente")
                self.assertIsNone(resultado)


class TestHaceCuanto(unittest.TestCase):
    """Tests para el calculo de tiempo relativo."""

    def test_none(self):
        self.assertEqual(_hace_cuanto(None), "sin datos")

    def test_ahora(self):
        resultado = _hace_cuanto(datetime.now())
        self.assertIn("ahora", resultado)

    def test_hace_minutos(self):
        hace_10_min = datetime.now() - timedelta(minutes=10)
        resultado = _hace_cuanto(hace_10_min)
        self.assertIn("min", resultado)

    def test_hace_horas(self):
        hace_3h = datetime.now() - timedelta(hours=3)
        resultado = _hace_cuanto(hace_3h)
        self.assertIn("h", resultado)

    def test_hace_dias(self):
        hace_5d = datetime.now() - timedelta(days=5)
        resultado = _hace_cuanto(hace_5d)
        self.assertIn("5 dias", resultado)


class TestDetectarServicios(unittest.TestCase):
    """Tests para la deteccion de servicios systemd (mock de systemctl)."""

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas.subprocess.run")
    def test_detecta_timer(self, mock_run, mock_ruta):
        # Simular directorio del ecosistema con una automatizacion
        tmpdir = tempfile.mkdtemp()
        eco = Path(tmpdir)
        auto_dir = eco / "centinela_archivos"
        auto_dir.mkdir()
        (auto_dir / "centinela_archivos.py").write_text("# script")

        mock_ruta.__class__ = Path
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(eco.iterdir())

        # Mock de systemctl list-timers
        mock_result_timers = MagicMock()
        mock_result_timers.stdout = (
            "NEXT                            LEFT     LAST                            PASSED   UNIT                     ACTIVATES\n"
            "Wed 2026-05-27 12:40:33 CEST    37min    Wed 2026-05-27 11:40:33 CEST    22min ago centinela_archivos.timer centinela_archivos.service\n"
        )

        # Mock de systemctl list-units (para daemons)
        mock_result_units = MagicMock()
        mock_result_units.stdout = ""

        def side_effect(cmd, **kwargs):
            if "list-timers" in cmd:
                return mock_result_timers
            return mock_result_units

        mock_run.side_effect = side_effect

        servicios = detectar_servicios()

        import shutil
        shutil.rmtree(tmpdir)

        timer_names = [s["nombre"] for s in servicios]
        self.assertIn("centinela_archivos.timer", timer_names)

    @patch("jefe_de_maquinas.RUTA_ECOSISTEMA")
    @patch("jefe_de_maquinas.subprocess.run")
    def test_detecta_daemon(self, mock_run, mock_ruta):
        tmpdir = tempfile.mkdtemp()
        eco = Path(tmpdir)
        auto_dir = eco / "cronista_errores"
        auto_dir.mkdir()
        (auto_dir / "cronista_errores.py").write_text("# script")

        mock_ruta.__class__ = Path
        mock_ruta.is_dir.return_value = True
        mock_ruta.iterdir.return_value = list(eco.iterdir())

        # No timers para este servicio
        mock_result_timers = MagicMock()
        mock_result_timers.stdout = ""

        # Si como service
        mock_result_units = MagicMock()
        mock_result_units.stdout = (
            "  cronista_errores.service loaded active running Cronista de Errores\n"
        )

        # Mock de show --property=MainPID
        mock_pid = MagicMock()
        mock_pid.stdout = "MainPID=9216\n"

        def side_effect(cmd, **kwargs):
            if "list-timers" in cmd:
                return mock_result_timers
            if "list-units" in cmd:
                return mock_result_units
            if "show" in cmd:
                return mock_pid
            return MagicMock(stdout="")

        mock_run.side_effect = side_effect

        servicios = detectar_servicios()

        import shutil
        shutil.rmtree(tmpdir)

        daemon_names = [s["nombre"] for s in servicios]
        self.assertIn("cronista_errores.service", daemon_names)

        daemon = next(s for s in servicios if s["nombre"] == "cronista_errores.service")
        self.assertEqual(daemon["estado"], "running")
        self.assertIn("9216", daemon["detalle"])


class TestErroresRecientes(unittest.TestCase):
    """Tests para la lectura de errores recientes."""

    def test_lee_errores_24h(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir)
            log_global = logs_dir / "errores_global.log"

            ahora = datetime.now()
            hace_1h = (ahora - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            hace_2d = (ahora - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")

            log_global.write_text(
                f"{hace_2d} | limpiador | Error viejo\n"
                f"{hace_1h} | actualizador | Error reciente\n"
            )

            with patch("jefe_de_maquinas.RUTA_LOGS", logs_dir):
                errores = errores_recientes_24h()

            self.assertEqual(len(errores), 1)
            self.assertEqual(errores[0]["automatizacion"], "actualizador")

    def test_sin_log_global(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("jefe_de_maquinas.RUTA_LOGS", Path(tmpdir)):
                errores = errores_recientes_24h()
            self.assertEqual(errores, [])


class TestCmdLogs(unittest.TestCase):
    """Tests para el comando de mostrar logs."""

    def test_muestra_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir)
            log = logs_dir / "limpiador.log"
            log.write_text(
                "2026-05-27 10:00:00,123 | INFO | limpiador | Linea 1\n"
                "2026-05-27 10:01:00,456 | ERROR | limpiador | Error grave\n"
            )

            with patch("jefe_de_maquinas.RUTA_LOGS", logs_dir):
                # No deberia lanzar excepcion
                cmd_logs("limpiador", lineas=10)

    def test_log_no_existe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("jefe_de_maquinas.RUTA_LOGS", Path(tmpdir)):
                with self.assertRaises(SystemExit) as ctx:
                    cmd_logs("inexistente")
                self.assertEqual(ctx.exception.code, 1)


class TestCmdEjecutar(unittest.TestCase):
    """Tests para la ejecucion manual de automatizaciones."""

    @patch("jefe_de_maquinas.subprocess.run")
    def test_ejecuta_script(self, mock_run):
        with tempfile.TemporaryDirectory() as tmpdir:
            eco = Path(tmpdir)
            auto_dir = eco / "limpiador"
            auto_dir.mkdir()
            script = auto_dir / "limpiador.py"
            script.write_text("# script de prueba")

            mock_run.return_value = MagicMock(returncode=0)

            with patch("jefe_de_maquinas.RUTA_ECOSISTEMA", eco):
                from jefe_de_maquinas import cmd_ejecutar
                cmd_ejecutar("limpiador")

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            self.assertIn(str(script), str(call_args))

    def test_auto_no_existe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("jefe_de_maquinas.RUTA_ECOSISTEMA", Path(tmpdir)):
                from jefe_de_maquinas import cmd_ejecutar
                with self.assertRaises(SystemExit):
                    cmd_ejecutar("inexistente_total")


class TestCmdResumen(unittest.TestCase):
    """Tests para el resumen corto."""

    @patch("jefe_de_maquinas.notificar")
    @patch("jefe_de_maquinas.errores_recientes_24h", return_value=[])
    @patch("jefe_de_maquinas.detectar_servicios", return_value=[])
    @patch("jefe_de_maquinas.descubrir_automatizaciones")
    @patch("jefe_de_maquinas.cargar_config", return_value={"notificacion": {"duracion": 5000}})
    def test_genera_resumen(self, mock_cfg, mock_desc, mock_srv, mock_err, mock_notif):
        mock_desc.return_value = [
            {"implementada": True},
            {"implementada": True},
            {"implementada": False},
        ]

        texto = cmd_resumen()

        self.assertIn("2 activas", texto)
        self.assertIn("1 pendientes", texto)
        self.assertIn("Sin errores", texto)
        mock_notif.assert_called_once()


class TestExtraerDetalleTimer(unittest.TestCase):
    """Tests para el parseo de lineas de list-timers."""

    def test_con_left(self):
        linea = "Wed 2026-05-27 12:40:33 CEST 37min left Wed 2026-05-27 11:40:33 CEST 22min ago centinela_archivos.timer"
        resultado = _extraer_detalle_timer(linea)
        self.assertIn("37min", resultado)

    def test_sin_next(self):
        linea = "- - centinela.timer"
        resultado = _extraer_detalle_timer(linea)
        self.assertEqual(resultado, "boot")


# ── R6-005: --logs "" y --ejecutar "" deben fallar con exit≠0 ────────────────

class TestR6005LogsEjecutarVacios(unittest.TestCase):
    """R6-005: argumento vacio para --logs / --ejecutar → exit≠0."""

    def _run_main(self, argv):
        """Ejecuta main() con los args dados y captura el SystemExit."""
        import io
        from jefe_de_maquinas import main
        with patch("sys.argv", ["castillo"] + argv):
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", new_callable=io.StringIO):
                    main()
        return ctx.exception.code

    def test_logs_vacio_sale_nonzero(self):
        code = self._run_main(["--logs", ""])
        self.assertNotEqual(code, 0)

    def test_ejecutar_vacio_sale_nonzero(self):
        code = self._run_main(["--ejecutar", ""])
        self.assertNotEqual(code, 0)

    def test_logs_solo_espacios_sale_nonzero(self):
        code = self._run_main(["--logs", "   "])
        self.assertNotEqual(code, 0)

    def test_ejecutar_solo_espacios_sale_nonzero(self):
        code = self._run_main(["--ejecutar", "   "])
        self.assertNotEqual(code, 0)


# ── R6-004: --parar / --arrancar / --ejecutar con servicio inexistente → exit≠0 ─

class TestR6004ParaArrancarError(unittest.TestCase):
    """R6-004: parar/arrancar/ejecutar con nombre inexistente → exit≠0."""

    @patch("jefe_de_maquinas.subprocess.run")
    def test_parar_inexistente_sale_1(self, mock_run):
        """systemctl stop falla → sys.exit(1)."""
        mock_run.return_value = MagicMock(returncode=5, stderr="Unit not found.")
        from jefe_de_maquinas import cmd_parar
        with self.assertRaises(SystemExit) as ctx:
            cmd_parar("servicio_que_no_existe_xyz.service")
        self.assertEqual(ctx.exception.code, 1)

    @patch("jefe_de_maquinas.subprocess.run")
    def test_arrancar_inexistente_sale_1(self, mock_run):
        """systemctl start falla → sys.exit(1)."""
        mock_run.return_value = MagicMock(returncode=5, stderr="Unit not found.")
        from jefe_de_maquinas import cmd_arrancar
        with self.assertRaises(SystemExit) as ctx:
            cmd_arrancar("servicio_que_no_existe_xyz.service")
        self.assertEqual(ctx.exception.code, 1)

    @patch("jefe_de_maquinas.subprocess.run")
    def test_parar_existente_no_sale(self, mock_run):
        """systemctl stop OK → no sys.exit (camino feliz sin regresion)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        from jefe_de_maquinas import cmd_parar
        # No debe lanzar SystemExit
        cmd_parar("limpiador.service")

    @patch("jefe_de_maquinas.subprocess.run")
    def test_arrancar_existente_no_sale(self, mock_run):
        """systemctl start OK → no sys.exit (camino feliz sin regresion)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        from jefe_de_maquinas import cmd_arrancar
        cmd_arrancar("limpiador.service")

    @patch("jefe_de_maquinas.subprocess.run")
    def test_ejecutar_termina_con_error_sale_1(self, mock_run):
        """Script que sale con returncode≠0 → sys.exit(1)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            eco = Path(tmpdir)
            auto_dir = eco / "falla_siempre"
            auto_dir.mkdir()
            (auto_dir / "falla_siempre.py").write_text("# script")
            mock_run.return_value = MagicMock(returncode=1)
            from jefe_de_maquinas import cmd_ejecutar
            with patch("jefe_de_maquinas.RUTA_ECOSISTEMA", eco):
                with self.assertRaises(SystemExit) as ctx:
                    cmd_ejecutar("falla_siempre")
            self.assertEqual(ctx.exception.code, 1)

    @patch("jefe_de_maquinas.subprocess.run")
    def test_ejecutar_ok_no_sale(self, mock_run):
        """Script que sale con returncode==0 → no sys.exit (camino feliz)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            eco = Path(tmpdir)
            auto_dir = eco / "exitoso"
            auto_dir.mkdir()
            (auto_dir / "exitoso.py").write_text("# script")
            mock_run.return_value = MagicMock(returncode=0)
            from jefe_de_maquinas import cmd_ejecutar
            with patch("jefe_de_maquinas.RUTA_ECOSISTEMA", eco):
                cmd_ejecutar("exitoso")  # no debe lanzar SystemExit


if __name__ == "__main__":
    unittest.main()
