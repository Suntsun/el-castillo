#!/usr/bin/env python3
"""Tests para cronista_informes."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cronista_informes import (
    contar_actividad,
    contar_errores_global,
    contar_especificos,
    generar_informe,
    guardar_informe,
    obtener_disco,
    obtener_temperatura,
    obtener_uptime,
    obtener_timers,
    _estado_global,
    _severidad_notificacion,
    enviar_notificacion,
)


class TestEstadoGlobal(unittest.TestCase):
    """Tests para la determinación del estado global."""

    def test_verde_sin_errores(self):
        estado, desc = _estado_global(0)
        self.assertEqual(estado, "VERDE")
        self.assertIn("orden", desc)

    def test_amarillo_pocos_errores(self):
        estado, _ = _estado_global(3)
        self.assertEqual(estado, "AMARILLO")

    def test_rojo_muchos_errores(self):
        estado, _ = _estado_global(10)
        self.assertEqual(estado, "ROJO")

    def test_amarillo_limite(self):
        estado, _ = _estado_global(5)
        self.assertEqual(estado, "AMARILLO")

    def test_rojo_desde_seis(self):
        estado, _ = _estado_global(6)
        self.assertEqual(estado, "ROJO")


class TestSeveridadNotificacion(unittest.TestCase):
    """Tests para severidad de notificación."""

    def test_exito_sin_errores(self):
        self.assertEqual(_severidad_notificacion(0), "exito")

    def test_aviso_con_pocos(self):
        self.assertEqual(_severidad_notificacion(3), "aviso")

    def test_error_con_muchos(self):
        self.assertEqual(_severidad_notificacion(10), "error")


class TestContarActividad(unittest.TestCase):
    """Tests para el conteo de actividad en logs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta_logs = Path(self.tmpdir)
        self.ahora = datetime(2026, 5, 27, 12, 0, 0)
        self.desde = self.ahora - timedelta(days=7)

    def _escribir_log(self, nombre: str, lineas: list[str]):
        ruta = self.ruta_logs / f"{nombre}.log"
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    @patch("cronista_informes.RUTA_LOGS")
    def test_cuenta_lineas_info(self, mock_logs):
        mock_logs.__class__ = Path
        mock_logs.exists = MagicMock(return_value=True)
        mock_logs.glob = MagicMock(return_value=[
            self.ruta_logs / "actualizador.log"
        ])

        self._escribir_log("actualizador", [
            "2026-05-26 14:01:09,242 | INFO | actualizador | Mensaje 1",
            "2026-05-26 14:01:09,242 | INFO | actualizador | Mensaje 2",
            "2026-05-26 14:01:09,242 | ERROR | actualizador | Error aqui",
        ])

        conteo = contar_actividad(self.desde, self.ahora)
        self.assertEqual(conteo.get("actualizador", 0), 2)

    @patch("cronista_informes.RUTA_LOGS")
    def test_excluye_logs_internos(self, mock_logs):
        mock_logs.__class__ = Path
        mock_logs.exists = MagicMock(return_value=True)

        # Crear los archivos reales para que stem funcione
        self._escribir_log("cronista_errores", [
            "2026-05-26 14:01:09,242 | INFO | cronista_errores | Interno",
        ])
        self._escribir_log("test_comun", [
            "2026-05-26 14:01:09,242 | INFO | test_comun | Test",
        ])

        mock_logs.glob = MagicMock(return_value=[
            self.ruta_logs / "cronista_errores.log",
            self.ruta_logs / "test_comun.log",
        ])

        conteo = contar_actividad(self.desde, self.ahora)
        self.assertNotIn("cronista_errores", conteo)
        self.assertNotIn("test_comun", conteo)

    @patch("cronista_informes.RUTA_LOGS")
    def test_ignora_lineas_fuera_de_rango(self, mock_logs):
        mock_logs.__class__ = Path
        mock_logs.exists = MagicMock(return_value=True)

        self._escribir_log("limpiador", [
            "2026-05-10 14:01:09,242 | INFO | limpiador | Muy vieja",
            "2026-05-26 14:01:09,242 | INFO | limpiador | Dentro del rango",
        ])

        mock_logs.glob = MagicMock(return_value=[
            self.ruta_logs / "limpiador.log",
        ])

        conteo = contar_actividad(self.desde, self.ahora)
        self.assertEqual(conteo.get("limpiador", 0), 1)

    @patch("cronista_informes.RUTA_LOGS")
    def test_sin_logs_devuelve_vacio(self, mock_logs):
        mock_logs.__class__ = Path
        mock_logs.exists = MagicMock(return_value=False)

        conteo = contar_actividad(self.desde, self.ahora)
        self.assertEqual(conteo, {})


class TestContarErroresGlobal(unittest.TestCase):
    """Tests para el parseo de errores_global.log."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta_logs = Path(self.tmpdir)
        self.ahora = datetime(2026, 5, 27, 12, 0, 0)
        self.desde = self.ahora - timedelta(days=7)

    @patch("cronista_informes.RUTA_LOGS")
    def test_agrupa_errores_por_automatizacion(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name
        mock_logs.glob = MagicMock(return_value=[])

        ruta_global = self.ruta_logs / "errores_global.log"
        ruta_global.write_text(
            "2026-05-26 14:01:09 | actualizador | Error de pacman\n"
            "2026-05-26 14:01:10 | actualizador | Error de pacman\n"
            "2026-05-26 14:02:00 | cazador_medios | yt-dlp no encontrado\n",
            encoding="utf-8",
        )

        errores = contar_errores_global(self.desde, self.ahora)
        self.assertEqual(len(errores["actualizador"]), 2)
        self.assertEqual(len(errores["cazador_medios"]), 1)

    @patch("cronista_informes.RUTA_LOGS")
    def test_sin_errores_devuelve_vacio(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name
        mock_logs.glob = MagicMock(return_value=[])

        # No crear el archivo
        errores = contar_errores_global(self.desde, self.ahora)
        self.assertEqual(errores, {})


class TestContarEspecificos(unittest.TestCase):
    """Tests para el conteo de actividad específica."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta_logs = Path(self.tmpdir)
        self.ahora = datetime(2026, 5, 27, 12, 0, 0)
        self.desde = self.ahora - timedelta(days=7)

    @patch("cronista_informes.RUTA_LOGS")
    def test_cuenta_wallpapers(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name

        ruta = self.ruta_logs / "tejedor_entorno.log"
        ruta.write_text(
            "2026-05-26 20:45:09,408 | INFO | tejedor_entorno | Wallpaper cambiado correctamente\n"
            "2026-05-26 20:45:33,317 | INFO | tejedor_entorno | Wallpaper aplicado correctamente\n"
            "2026-05-26 20:45:03,886 | ERROR | tejedor_entorno | Comando no encontrado\n",
            encoding="utf-8",
        )

        especificos = contar_especificos(self.desde, self.ahora)
        self.assertEqual(especificos.get("wallpapers", 0), 2)

    @patch("cronista_informes.RUTA_LOGS")
    def test_cuenta_traducciones(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name

        ruta = self.ruta_logs / "traductor_terminal.log"
        ruta.write_text(
            "2026-05-26 19:44:16,628 | INFO | traductor_terminal | Traducción completada: 'hello' -> 'hola'\n"
            "2026-05-26 19:44:59,462 | INFO | traductor_terminal | Traducción completada: 'bye' -> 'adios'\n",
            encoding="utf-8",
        )

        especificos = contar_especificos(self.desde, self.ahora)
        self.assertEqual(especificos.get("traducciones", 0), 2)

    @patch("cronista_informes.RUTA_LOGS")
    def test_cuenta_descargas(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name

        ruta = self.ruta_logs / "cazador_medios.log"
        ruta.write_text(
            "2026-05-26 20:28:24,510 | INFO | cazador_medios | Descarga completada en /home/sun/Musica\n",
            encoding="utf-8",
        )

        especificos = contar_especificos(self.desde, self.ahora)
        self.assertEqual(especificos.get("descargas", 0), 1)

    @patch("cronista_informes.RUTA_LOGS")
    def test_cuenta_sesiones_zen(self, mock_logs):
        mock_logs.__truediv__ = lambda self_, name: Path(self.tmpdir) / name

        ruta = self.ruta_logs / "guardador_silencio.log"
        ruta.write_text(
            "2026-05-27 09:15:09,878 | INFO | guardador_silencio | Modo Zen activado por 25 minutos\n"
            "2026-05-27 10:01:04,770 | INFO | guardador_silencio | Modo Zen activado por 25 minutos\n",
            encoding="utf-8",
        )

        especificos = contar_especificos(self.desde, self.ahora)
        self.assertEqual(especificos.get("sesiones_zen", 0), 2)


class TestObtenerDisco(unittest.TestCase):
    """Tests para obtención de datos de disco."""

    @patch("cronista_informes.shutil.disk_usage")
    def test_calcula_porcentajes(self, mock_usage):
        mock_usage.return_value = MagicMock(
            total=100 * 1024**3,
            used=9 * 1024**3,
            free=91 * 1024**3,
        )
        disco = obtener_disco()
        self.assertEqual(disco["porcentaje_usado"], 9)
        self.assertEqual(disco["porcentaje_libre"], 91)

    @patch("cronista_informes.shutil.disk_usage", side_effect=OSError("fallo"))
    def test_error_devuelve_negativo(self, mock_usage):
        disco = obtener_disco()
        self.assertEqual(disco["porcentaje_usado"], -1)


class TestObtenerTemperatura(unittest.TestCase):
    """Tests para lectura de temperatura."""

    def test_lee_temperatura_real(self):
        # Si /sys/class/thermal existe, debería devolver un entero o None
        temp = obtener_temperatura()
        if temp is not None:
            self.assertIsInstance(temp, int)
            self.assertGreater(temp, -50)
            self.assertLess(temp, 150)


class TestObtenerUptime(unittest.TestCase):
    """Tests para lectura de uptime."""

    @patch("cronista_informes.Path.read_text", return_value="180000.50 360000.00")
    def test_formatea_dias_horas(self, mock_read):
        resultado = obtener_uptime()
        self.assertIn("2 dias", resultado)
        self.assertIn("2 horas", resultado)

    @patch("cronista_informes.Path.read_text", return_value="3700.00 7000.00")
    def test_formatea_horas_minutos(self, mock_read):
        resultado = obtener_uptime()
        self.assertIn("1 hora", resultado)

    @patch("cronista_informes.Path.read_text", side_effect=OSError("fallo"))
    def test_error_devuelve_desconocido(self, mock_read):
        resultado = obtener_uptime()
        self.assertEqual(resultado, "desconocido")


class TestGenerarInforme(unittest.TestCase):
    """Tests para la generación completa del informe."""

    @patch("cronista_informes.obtener_timers", return_value=(4, ["a.timer"]))
    @patch("cronista_informes.obtener_uptime", return_value="2 dias 4 horas")
    @patch("cronista_informes.obtener_temperatura", return_value=17)
    @patch("cronista_informes.obtener_disco", return_value={
        "porcentaje_usado": 9, "porcentaje_libre": 91,
        "total_gb": 100, "usado_gb": 9, "libre_gb": 91,
    })
    @patch("cronista_informes.contar_especificos", return_value={
        "wallpapers": 5, "traducciones": 3,
    })
    @patch("cronista_informes.contar_errores_global", return_value={
        "actualizador": ["Error de pacman", "Error de pacman"],
    })
    @patch("cronista_informes.contar_actividad", return_value={
        "actualizador": 12, "limpiador": 2,
    })
    def test_informe_contiene_secciones(self, *mocks):
        config = {}
        fecha = datetime(2026, 5, 27, 21, 0, 0)
        texto = generar_informe(config, fecha)

        self.assertIn("INFORME SEMANAL", texto)
        self.assertIn("ACTIVIDAD", texto)
        self.assertIn("ERRORES", texto)
        self.assertIn("SISTEMA", texto)
        self.assertIn("actualizador", texto)
        self.assertIn("12", texto)
        self.assertIn("9%", texto)
        self.assertIn("17 C", texto)
        self.assertIn("2 dias 4 horas", texto)

    @patch("cronista_informes.obtener_timers", return_value=(0, []))
    @patch("cronista_informes.obtener_uptime", return_value="1 hora")
    @patch("cronista_informes.obtener_temperatura", return_value=None)
    @patch("cronista_informes.obtener_disco", return_value={
        "porcentaje_usado": 50, "porcentaje_libre": 50,
        "total_gb": 100, "usado_gb": 50, "libre_gb": 50,
    })
    @patch("cronista_informes.contar_especificos", return_value={})
    @patch("cronista_informes.contar_errores_global", return_value={})
    @patch("cronista_informes.contar_actividad", return_value={})
    def test_informe_sin_actividad(self, *mocks):
        config = {}
        fecha = datetime(2026, 5, 27, 21, 0, 0)
        texto = generar_informe(config, fecha)

        self.assertIn("VERDE", texto)
        self.assertIn("Sin errores", texto)
        self.assertIn("Sin actividad", texto)


class TestGuardarInforme(unittest.TestCase):
    """Tests para guardar informes en disco."""

    def test_guarda_informe_correcto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"informe": {"ruta_informes": tmpdir}}
            fecha = datetime(2026, 5, 27)
            texto = "INFORME DE PRUEBA"

            ruta = guardar_informe(texto, config, fecha)

            self.assertTrue(ruta.exists())
            self.assertEqual(ruta.name, "informe_2026-05-27.txt")
            self.assertEqual(ruta.read_text(), texto)


class TestEnviarNotificacion(unittest.TestCase):
    """Tests para el envío de notificación."""

    @patch("cronista_informes.notificar")
    def test_notifica_exito_sin_errores(self, mock_notificar):
        actividad = {"actualizador": 10}
        errores = {}
        disco = {"porcentaje_usado": 9}
        config = {"notificacion": {"duracion": 10000}}

        enviar_notificacion(actividad, errores, disco, config)

        mock_notificar.assert_called_once()
        args = mock_notificar.call_args
        self.assertIn("10 ejecuciones", args[0][1])
        self.assertIn("0 errores", args[0][1])
        self.assertEqual(args[0][2], "exito")

    @patch("cronista_informes.notificar")
    def test_notifica_aviso_con_errores(self, mock_notificar):
        actividad = {"actualizador": 10}
        errores = {"actualizador": ["error1", "error2"]}
        disco = {"porcentaje_usado": 9}
        config = {}

        enviar_notificacion(actividad, errores, disco, config)

        args = mock_notificar.call_args
        self.assertEqual(args[0][2], "aviso")


class TestCmdMostrarFecha(unittest.TestCase):
    """ERR-009b: validar formato de fecha en --semana."""

    def _config(self, tmpdir):
        return {"informe": {"ruta_informes": str(tmpdir)}}

    def test_formato_invalido_sale_con_error(self):
        import tempfile
        from cronista_informes import cmd_mostrar
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            with self.assertRaises(SystemExit) as ctx:
                cmd_mostrar(config, "no-es-fecha")
            self.assertNotEqual(ctx.exception.code, 0)

    def test_formato_dd_mm_yyyy_invalido(self):
        import tempfile
        from cronista_informes import cmd_mostrar
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            with self.assertRaises(SystemExit) as ctx:
                cmd_mostrar(config, "26-05-2025")
            self.assertNotEqual(ctx.exception.code, 0)

    def test_formato_valido_inexistente_sale_1(self):
        """Fecha con formato correcto pero informe inexistente → exit 1 (no encontrado)."""
        import tempfile
        from cronista_informes import cmd_mostrar
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            with self.assertRaises(SystemExit) as ctx:
                cmd_mostrar(config, "2025-05-26")
            self.assertNotEqual(ctx.exception.code, 0)

    def test_formato_valido_existente_muestra_informe(self):
        """Fecha válida con informe existente → muestra contenido sin error."""
        import tempfile
        import io
        from cronista_informes import cmd_mostrar
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(tmpdir)
            ruta = Path(tmpdir) / "informe_2025-05-26.txt"
            ruta.write_text("Informe de prueba\n", encoding="utf-8")
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                cmd_mostrar(config, "2025-05-26")
            self.assertIn("Informe de prueba", mock_out.getvalue())


class TestMainRoutingSemana(unittest.TestCase):
    """ERR-S01: routing de --semana en main distingue None, "" y valor válido."""

    def _run_main(self, argv):
        """Ejecuta main() con los argv dados; devuelve el código de salida (o None)."""
        import cronista_informes as mod
        with patch("sys.argv", argv):
            with patch.object(mod, "cargar_config", return_value={}):
                try:
                    mod.main()
                    return None
                except SystemExit as e:
                    return e.code

    def test_semana_vacia_sale_2(self):
        """--semana '' → error de usuario, exit 2."""
        codigo = self._run_main(["informe", "--semana", ""])
        self.assertEqual(codigo, 2)

    def test_semana_formato_invalido_sale_non_zero(self):
        """--semana con formato inválido (lo detecta cmd_mostrar) → exit≠0."""
        codigo = self._run_main(["informe", "--semana", "no-es-fecha"])
        self.assertIsNotNone(codigo)
        self.assertNotEqual(codigo, 0)

    def test_sin_semana_llama_cmd_mostrar_sin_arg(self):
        """Sin --semana se llama cmd_mostrar(config) sin segundo argumento."""
        import cronista_informes as mod
        with patch("sys.argv", ["informe"]):
            with patch.object(mod, "cargar_config", return_value={}):
                with patch.object(mod, "cmd_mostrar") as mock_cmd:
                    mod.main()
        mock_cmd.assert_called_once_with({})


if __name__ == "__main__":
    unittest.main()
