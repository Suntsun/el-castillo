#!/usr/bin/env python3
"""Tests para explorador_archivos."""

import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from explorador_archivos.explorador_archivos import (
    verificar_herramienta,
    verificar_dependencias,
    buscar_archivos,
    buscar_contenido,
    buscar_historial,
    buscar_logs,
    buscar_logs_excluyendo,
    _abreviar_home,
)


class TestVerificarHerramientas(TestCase):
    @patch("explorador_archivos.explorador_archivos.shutil.which")
    def test_herramienta_disponible(self, mock_which):
        mock_which.return_value = "/usr/bin/fd"
        self.assertTrue(verificar_herramienta("fd"))

    @patch("explorador_archivos.explorador_archivos.shutil.which")
    def test_herramienta_no_disponible(self, mock_which):
        mock_which.return_value = None
        self.assertFalse(verificar_herramienta("fd"))

    @patch("explorador_archivos.explorador_archivos.shutil.which")
    def test_verificar_dependencias_todo_ok(self, mock_which):
        mock_which.return_value = "/usr/bin/algo"
        ausentes = verificar_dependencias()
        self.assertEqual(ausentes, [])

    @patch("explorador_archivos.explorador_archivos.shutil.which")
    def test_verificar_dependencias_falta_fd(self, mock_which):
        def side_effect(nombre):
            return None if nombre == "fd" else "/usr/bin/rg"
        mock_which.side_effect = side_effect
        ausentes = verificar_dependencias()
        self.assertIn("fd", ausentes)


class TestBuscarArchivos(TestCase):
    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_exitosa(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/home/usuario/test.py\n/home/usuario/docs/test.txt\n",
            stderr="",
        )
        resultados = buscar_archivos("test", "/home/usuario", [".cache"], 10, 15)
        self.assertEqual(len(resultados), 2)
        self.assertIn("/home/usuario/test.py", resultados)
        mock_run.assert_called_once()
        # Verificar que --exclude .cache está en los argumentos
        args_cmd = mock_run.call_args[0][0]
        idx = args_cmd.index("--exclude")
        self.assertEqual(args_cmd[idx + 1], ".cache")

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_sin_resultados(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        resultados = buscar_archivos("inexistente", "/home/usuario", [], 10, 15)
        self.assertEqual(resultados, [])

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="fd", timeout=15)
        resultados = buscar_archivos("test", "/home/usuario", [], 10, 15)
        self.assertEqual(resultados, [])

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_con_limite(self, mock_run):
        lineas = "\n".join([f"/home/usuario/file{i}.txt" for i in range(20)])
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=lineas,
            stderr="",
        )
        resultados = buscar_archivos("file", "/home/usuario", [], 5, 15)
        self.assertEqual(len(resultados), 5)

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_sin_limite(self, mock_run):
        lineas = "\n".join([f"/home/usuario/file{i}.txt" for i in range(20)])
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=lineas,
            stderr="",
        )
        resultados = buscar_archivos("file", "/home/usuario", [], 0, 15)
        self.assertEqual(len(resultados), 20)


class TestBuscarContenido(TestCase):
    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_contenido_ok(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/home/usuario/notas.txt:5:algo con python aquí\n",
            stderr="",
        )
        resultados = buscar_contenido("python", "/home/usuario", [".cache"], 10, 15)
        self.assertEqual(len(resultados), 1)
        mock_run.assert_called_once()
        # Verificar que --glob !.cache está en los argumentos
        args_cmd = mock_run.call_args[0][0]
        idx = args_cmd.index("--glob")
        self.assertEqual(args_cmd[idx + 1], "!.cache")

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_contenido_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="rg", timeout=15)
        resultados = buscar_contenido("test", "/home/usuario", [], 10, 15)
        self.assertEqual(resultados, [])


class TestBuscarHistorial(TestCase):
    def test_busqueda_historial_ok(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".history",
                                         delete=False) as f:
            f.write("ls -la\n")
            f.write("git status\n")
            f.write("python3 main.py\n")
            f.write("git log\n")
            f.write("python3 test.py\n")
            ruta = f.name

        resultados = buscar_historial("python", ruta, 10)
        self.assertEqual(len(resultados), 2)
        # Más recientes primero (reversed)
        self.assertEqual(resultados[0], "python3 test.py")
        self.assertEqual(resultados[1], "python3 main.py")

        Path(ruta).unlink()

    def test_busqueda_historial_con_limite(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".history",
                                         delete=False) as f:
            for i in range(20):
                f.write(f"python script{i}.py\n")
            ruta = f.name

        resultados = buscar_historial("python", ruta, 3)
        self.assertEqual(len(resultados), 3)

        Path(ruta).unlink()

    def test_busqueda_historial_sin_duplicados(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".history",
                                         delete=False) as f:
            f.write("git status\n")
            f.write("git status\n")
            f.write("git status\n")
            ruta = f.name

        resultados = buscar_historial("git", ruta, 10)
        self.assertEqual(len(resultados), 1)

        Path(ruta).unlink()

    def test_busqueda_historial_fichero_no_existe(self):
        resultados = buscar_historial("test", "/tmp/no_existe_history_xyz", 10)
        self.assertEqual(resultados, [])


class TestBuscarLogs(TestCase):
    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_busqueda_logs_ok(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="actualizador.log:45:openai updated\n",
            stderr="",
        )
        with patch("explorador_archivos.explorador_archivos.Path.exists",
                    return_value=True):
            resultados = buscar_logs("openai", "/tmp/logs", 10, 15)
        self.assertEqual(len(resultados), 1)

    def test_busqueda_logs_directorio_no_existe(self):
        resultados = buscar_logs("test", "/tmp/no_existe_logs_xyz", 10, 15)
        self.assertEqual(resultados, [])


class TestAbreviarHome(TestCase):
    def test_abrevia_ruta_home(self):
        home = str(Path.home())
        resultado = _abreviar_home(f"{home}/Documentos/notas.txt")
        self.assertEqual(resultado, "~/Documentos/notas.txt")

    def test_no_abrevia_otra_ruta(self):
        resultado = _abreviar_home("/etc/config.txt")
        self.assertEqual(resultado, "/etc/config.txt")


class TestBuscarLogsExcluyendo(TestCase):
    """ERR-009a: en búsqueda general se excluye el dir de logs; en --logs solo el log propio."""

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_excluye_log_propio_por_ruta(self, mock_run):
        """buscar_logs_excluyendo filtra post-búsqueda las líneas del log propio.

        rg no soporta rutas absolutas en --glob (las ignora silenciosamente),
        así que la exclusión se aplica sobre el resultado de subprocess.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            log_propio = str(Path(tmpdir) / "explorador_archivos.log")
            otro_log = str(Path(tmpdir) / "otro.log")
            # rg devuelve líneas de ambos logs (incluyendo el que hay que excluir)
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=(
                    f"{log_propio}:1:termino_buscado\n"
                    f"{otro_log}:3:termino_buscado\n"
                ),
            )
            resultados = buscar_logs_excluyendo("termino", tmpdir, [log_propio], 0, 10)
            # La línea del log propio debe haber sido filtrada
            self.assertEqual(len(resultados), 1)
            self.assertIn(otro_log, resultados[0])
            self.assertNotIn("explorador_archivos.log", resultados[0])

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_directorio_inexistente_devuelve_vacio(self, mock_run):
        """Si el dir de logs no existe, devuelve lista vacía sin llamar rg."""
        resultado = buscar_logs_excluyendo("algo", "/tmp/no_existe_logs_xyz", [], 0, 10)
        self.assertEqual(resultado, [])
        mock_run.assert_not_called()

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_sin_exclusiones_devuelve_todo(self, mock_run):
        """Sin excluir_archivos, devuelve todas las líneas tal cual."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=f"{tmpdir}/a.log:1:algo\n{tmpdir}/b.log:2:algo\n",
            )
            resultados = buscar_logs_excluyendo("algo", tmpdir, [], 0, 10)
            self.assertEqual(len(resultados), 2)


class TestBuscarContenidoExcluirRutas(TestCase):
    """ERR-009a: buscar_contenido filtra post-búsqueda rutas absolutas excluidas."""

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_excluye_directorio_por_ruta_absoluta(self, mock_run):
        """Líneas cuya ruta empieza por una ruta excluida son filtradas."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "/home/usuario/proyecto/archivo.py:5:contenido\n"
                "/home/usuario/Escritorio/automatizaciones/logs/explorador_archivos.log:3:contenido\n"
            ),
        )
        excluir_rutas = ["/home/usuario/Escritorio/automatizaciones/logs"]
        resultados = buscar_contenido(
            "contenido", "/home/usuario", [], 0, 10, excluir_rutas=excluir_rutas
        )
        self.assertEqual(len(resultados), 1)
        self.assertIn("archivo.py", resultados[0])
        self.assertNotIn("explorador_archivos.log", resultados[0])

    @patch("explorador_archivos.explorador_archivos.subprocess.run")
    def test_sin_excluir_rutas_devuelve_todo(self, mock_run):
        """Sin excluir_rutas, devuelve todas las líneas."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="/ruta/a.py:1:algo\n/ruta/b.py:2:algo\n",
        )
        resultados = buscar_contenido("algo", "/ruta", [], 0, 10)
        self.assertEqual(len(resultados), 2)


if __name__ == "__main__":
    main()
