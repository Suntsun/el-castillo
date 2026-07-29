#!/usr/bin/env python3
"""Tests para invocador_entorno."""

import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invocador_entorno import (
    cargar_modo,
    listar_modos,
    lanzar_app,
    lanzar_modo,
    parar_modo,
    crear_modo,
    cambiar_workspace,
    guardar_modo_activo,
    cargar_modo_activo,
    _necesita_shell,
    RUTA_MODOS,
    RUTA_MODO_ACTIVO,
    RUTA_PLANTILLA,
)

# Prefijo de patch: modulo real dentro del paquete
_MOD = "invocador_entorno"


class TestNecesitaShell(TestCase):
    """Verifica deteccion de comandos que necesitan shell."""

    def test_comando_simple(self):
        self.assertFalse(_necesita_shell("opera"))

    def test_comando_con_argumentos(self):
        self.assertTrue(_necesita_shell("xdg-open https://youtube.com"))

    def test_comando_con_pipe(self):
        self.assertTrue(_necesita_shell("echo hola | grep h"))

    def test_comando_con_redireccion(self):
        self.assertTrue(_necesita_shell("echo hola > /tmp/test"))

    def test_comando_con_tilde(self):
        self.assertTrue(_necesita_shell("xdg-open ~/Music"))


class TestCargarModo(TestCase):
    """Verifica carga de modos TOML."""

    def test_cargar_modo_existente(self):
        datos = cargar_modo("dev")
        self.assertIn("modo", datos)
        self.assertIn("apps", datos)
        self.assertEqual(datos["modo"]["nombre"], "dev")

    def test_cargar_modo_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            cargar_modo("modo_que_no_existe_xyz")

    def test_modo_tiene_apps(self):
        datos = cargar_modo("dev")
        self.assertGreater(len(datos["apps"]), 0)

    def test_app_tiene_comando(self):
        datos = cargar_modo("dev")
        for app in datos["apps"]:
            self.assertIn("comando", app)


class TestListarModos(TestCase):
    """Verifica listado de modos disponibles."""

    def test_lista_no_vacia(self):
        modos = listar_modos()
        self.assertGreater(len(modos), 0)

    def test_excluye_plantilla(self):
        modos = listar_modos()
        nombres = [m["archivo"] for m in modos]
        self.assertNotIn("plantilla.toml", nombres)

    def test_contiene_dev(self):
        modos = listar_modos()
        nombres = [m["nombre"] for m in modos]
        self.assertIn("dev", nombres)

    def test_estructura_modo(self):
        modos = listar_modos()
        for m in modos:
            self.assertIn("archivo", m)
            self.assertIn("nombre", m)
            self.assertIn("descripcion", m)


class TestCambiarWorkspace(TestCase):
    """Verifica cambio de workspace via hyprctl."""

    @patch(f"{_MOD}.subprocess.run")
    def test_cambio_exitoso(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        self.assertTrue(cambiar_workspace(2))
        mock_run.assert_called_once_with(
            ["hyprctl", "dispatch", "workspace", "2"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch(f"{_MOD}.subprocess.run")
    def test_cambio_fallo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        self.assertFalse(cambiar_workspace(2))

    @patch(f"{_MOD}.subprocess.run", side_effect=FileNotFoundError)
    def test_hyprctl_no_disponible(self, mock_run):
        self.assertFalse(cambiar_workspace(1))


class TestLanzarApp(TestCase):
    """Verifica lanzamiento de apps individuales."""

    @patch(f"{_MOD}.subprocess.Popen")
    def test_lanzar_comando_simple(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        pid = lanzar_app("opera")
        self.assertEqual(pid, 1234)
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], ["opera"])
        self.assertFalse(kwargs.get("shell", False))

    @patch(f"{_MOD}.subprocess.Popen")
    def test_lanzar_comando_con_args(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 5678
        mock_popen.return_value = mock_proc

        pid = lanzar_app("xdg-open https://youtube.com")
        self.assertEqual(pid, 5678)
        args, kwargs = mock_popen.call_args
        self.assertEqual(args[0], "xdg-open https://youtube.com")
        self.assertTrue(kwargs["shell"])

    @patch(f"{_MOD}.subprocess.Popen", side_effect=Exception("fallo"))
    def test_lanzar_fallo(self, mock_popen):
        pid = lanzar_app("app_rota")
        self.assertIsNone(pid)


class TestLanzarModo(TestCase):
    """Verifica lanzamiento completo de un modo."""

    @patch(f"{_MOD}.guardar_modo_activo")
    @patch(f"{_MOD}.cambiar_workspace", return_value=True)
    @patch(f"{_MOD}.lanzar_app")
    @patch(f"{_MOD}.time.sleep")
    def test_lanzar_dev(self, mock_sleep, mock_lanzar, mock_ws, mock_guardar):
        mock_lanzar.side_effect = [100, 101, 102, 103]

        estado = lanzar_modo("dev")
        self.assertEqual(estado["modo"], "dev")
        self.assertEqual(len(estado["pids"]), 4)
        self.assertEqual(mock_lanzar.call_count, 4)
        self.assertTrue(mock_ws.call_count >= 1)
        mock_guardar.assert_called_once()

    @patch(f"{_MOD}.guardar_modo_activo")
    @patch(f"{_MOD}.cambiar_workspace", return_value=True)
    @patch(f"{_MOD}.lanzar_app")
    @patch(f"{_MOD}.time.sleep")
    def test_lanzar_con_app_fallida(self, mock_sleep, mock_lanzar, mock_ws, mock_guardar):
        mock_lanzar.side_effect = [100, None, 102, 103]

        estado = lanzar_modo("dev")
        self.assertEqual(len(estado["pids"]), 3)

    def test_lanzar_modo_inexistente(self):
        with self.assertRaises(FileNotFoundError):
            lanzar_modo("modo_fantasma_xyz")


class TestPararModo(TestCase):
    """Verifica parada de modo activo."""

    @patch(f"{_MOD}.cargar_modo_activo", return_value=None)
    def test_parar_sin_modo_activo(self, mock_cargar):
        resultado = parar_modo()
        self.assertIsNone(resultado)

    @patch(f"{_MOD}.RUTA_MODO_ACTIVO")
    @patch(f"{_MOD}.cargar_modo_activo")
    @patch(f"{_MOD}.os.kill")
    def test_parar_modo_activo(self, mock_kill, mock_cargar, mock_ruta):
        mock_cargar.return_value = {
            "modo": "dev",
            "pids": [100, 101, 102],
            "timestamp": "2026-05-27T10:00:00",
        }
        mock_ruta.unlink.return_value = None

        nombre = parar_modo()
        self.assertEqual(nombre, "dev")
        self.assertEqual(mock_kill.call_count, 3)
        # Verifica que se envia SIGTERM
        for c in mock_kill.call_args_list:
            self.assertEqual(c[0][1], signal.SIGTERM)

    @patch(f"{_MOD}.RUTA_MODO_ACTIVO")
    @patch(f"{_MOD}.cargar_modo_activo")
    @patch(f"{_MOD}.os.kill", side_effect=ProcessLookupError)
    def test_parar_pids_muertos(self, mock_kill, mock_cargar, mock_ruta):
        mock_cargar.return_value = {
            "modo": "dev",
            "pids": [100, 101],
            "timestamp": "2026-05-27T10:00:00",
        }
        mock_ruta.unlink.return_value = None

        nombre = parar_modo()
        self.assertEqual(nombre, "dev")


class TestCrearModo(TestCase):
    """Verifica creacion de nuevos modos."""

    def test_crear_modo_nuevo(self):
        with tempfile.TemporaryDirectory() as d:
            with patch(f"{_MOD}.RUTA_MODOS", Path(d)), \
                 patch(f"{_MOD}.RUTA_PLANTILLA", RUTA_PLANTILLA):
                ruta = crear_modo("test_nuevo")
                self.assertTrue(ruta.exists())
                contenido = ruta.read_text()
                self.assertIn('nombre = "test_nuevo"', contenido)

    def test_crear_modo_existente(self):
        with self.assertRaises(FileExistsError):
            crear_modo("dev")


class TestModoActivo(TestCase):
    """Verifica guardado y carga de estado del modo activo."""

    def test_guardar_y_cargar(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            ruta_tmp = Path(f.name)
        try:
            estado = {"modo": "test", "pids": [1, 2], "timestamp": "2026-01-01T00:00:00"}
            with patch(f"{_MOD}.RUTA_MODO_ACTIVO", ruta_tmp):
                guardar_modo_activo(estado)
                cargado = cargar_modo_activo()
                self.assertEqual(cargado["modo"], "test")
                self.assertEqual(cargado["pids"], [1, 2])
        finally:
            ruta_tmp.unlink(missing_ok=True)

    def test_cargar_sin_archivo(self):
        with patch(f"{_MOD}.RUTA_MODO_ACTIVO", Path("/tmp/no_existe_xyz.json")):
            self.assertIsNone(cargar_modo_activo())


if __name__ == "__main__":
    main()
