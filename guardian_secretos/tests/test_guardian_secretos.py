#!/usr/bin/env python3
"""Tests para guardian_secretos."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian_secretos import (
    cmd_cifrar,
    cmd_descifrar,
    cmd_abrir,
    cmd_editar,
    cmd_estado,
    _cifrar_gpg,
    _descifrar_gpg,
    _shred_archivo,
    _ruta_boveda,
    _resolver_nombre,
    _formato_tamano,
)


def _config_test(boveda_path: str) -> dict:
    """Configuracion de prueba."""
    return {
        "boveda": {
            "ruta": boveda_path,
            "cipher_algo": "AES256",
            "editor": "cat",
        },
        "notificacion": {
            "duracion": 1000,
            "severidad": "info",
        },
    }


class TestCifrarGPG(TestCase):
    @patch("guardian_secretos.subprocess.run")
    def test_cifrar_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"secreto")
            origen = Path(f.name)
        destino = Path(f.name + ".gpg")
        try:
            ok = _cifrar_gpg(origen, destino, "clave123", "AES256")
            self.assertTrue(ok)
            mock_run.assert_called_once()
            cmd_args = mock_run.call_args[0][0]
            self.assertIn("--symmetric", cmd_args)
            self.assertIn("AES256", cmd_args)
        finally:
            origen.unlink(missing_ok=True)
            destino.unlink(missing_ok=True)

    @patch("guardian_secretos.subprocess.run")
    def test_cifrar_falla(self, mock_run):
        mock_run.return_value = MagicMock(returncode=2, stderr=b"error gpg")
        ok = _cifrar_gpg(Path("/tmp/x"), Path("/tmp/x.gpg"), "clave", "AES256")
        self.assertFalse(ok)

    @patch("guardian_secretos.subprocess.run")
    def test_cifrar_timeout(self, mock_run):
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="gpg", timeout=30)
        ok = _cifrar_gpg(Path("/tmp/x"), Path("/tmp/x.gpg"), "clave", "AES256")
        self.assertFalse(ok)

    @patch("guardian_secretos.subprocess.run")
    def test_cifrar_gpg_no_encontrado(self, mock_run):
        mock_run.side_effect = FileNotFoundError("gpg")
        ok = _cifrar_gpg(Path("/tmp/x"), Path("/tmp/x.gpg"), "clave", "AES256")
        self.assertFalse(ok)


class TestDescifrarGPG(TestCase):
    @patch("guardian_secretos.subprocess.run")
    def test_descifrar_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        ok = _descifrar_gpg(Path("/tmp/x.gpg"), Path("/tmp/x"), "clave123")
        self.assertTrue(ok)
        cmd_args = mock_run.call_args[0][0]
        self.assertIn("--decrypt", cmd_args)

    @patch("guardian_secretos.subprocess.run")
    def test_descifrar_contrasena_incorrecta(self, mock_run):
        mock_run.return_value = MagicMock(returncode=2, stderr=b"decryption failed")
        ok = _descifrar_gpg(Path("/tmp/x.gpg"), Path("/tmp/x"), "mala")
        self.assertFalse(ok)


class TestShredArchivo(TestCase):
    @patch("guardian_secretos.subprocess.run")
    def test_shred_ok(self, mock_run):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            ruta = Path(f.name)
        mock_run.return_value = MagicMock(returncode=0)
        ok = _shred_archivo(ruta)
        self.assertTrue(ok)
        mock_run.assert_called_once()

    def test_shred_archivo_no_existe(self):
        ok = _shred_archivo(Path("/tmp/no_existe_xyz"))
        self.assertTrue(ok)

    @patch("guardian_secretos.subprocess.run")
    def test_shred_falla_usa_unlink(self, mock_run):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            ruta = Path(f.name)
        mock_run.return_value = MagicMock(returncode=1)
        ok = _shred_archivo(ruta)
        self.assertTrue(ok)
        self.assertFalse(ruta.exists())


class TestRutaBoveda(TestCase):
    def test_crea_boveda_si_no_existe(self):
        with tempfile.TemporaryDirectory() as d:
            ruta_nueva = Path(d) / "boveda_test"
            config = {"boveda": {"ruta": str(ruta_nueva)}}
            resultado = _ruta_boveda(config)
            self.assertEqual(resultado, ruta_nueva)
            self.assertTrue(resultado.exists())
            # Verificar permisos 700
            self.assertEqual(oct(resultado.stat().st_mode & 0o777), oct(0o700))

    def test_boveda_ya_existe(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"boveda": {"ruta": d}}
            resultado = _ruta_boveda(config)
            self.assertEqual(resultado, Path(d))


class TestResolverNombre(TestCase):
    def test_sin_extension(self):
        boveda = Path("/tmp/boveda")
        r = _resolver_nombre("passwords", boveda)
        self.assertEqual(r, boveda / "passwords.gpg")

    def test_con_extension_gpg(self):
        boveda = Path("/tmp/boveda")
        r = _resolver_nombre("passwords.gpg", boveda)
        self.assertEqual(r, boveda / "passwords.gpg")


class TestFormatoTamano(TestCase):
    def test_bytes(self):
        self.assertEqual(_formato_tamano(500), "500 B")

    def test_kilobytes(self):
        self.assertIn("KB", _formato_tamano(2048))

    def test_megabytes(self):
        self.assertIn("MB", _formato_tamano(2 * 1024 * 1024))


class TestCmdCifrar(TestCase):
    @patch("guardian_secretos.notificar")
    @patch("guardian_secretos._shred_archivo", return_value=True)
    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_cifrar_archivo_ok(self, mock_pass, mock_shred, mock_notif):
        with tempfile.TemporaryDirectory() as boveda_dir:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
                f.write(b"datos sensibles")
                archivo = f.name

            def fake_cifrar(origen, destino, contrasena, cipher):
                destino.write_bytes(b"cifrado")
                return True

            config = _config_test(boveda_dir)
            with patch("guardian_secretos._cifrar_gpg", side_effect=fake_cifrar):
                resultado = cmd_cifrar(archivo, config)

            self.assertEqual(resultado, 0)
            mock_shred.assert_called_once()
            mock_notif.assert_called()
            # Limpiar por si queda
            Path(archivo).unlink(missing_ok=True)

    def test_cifrar_archivo_no_existe(self):
        with tempfile.TemporaryDirectory() as boveda_dir:
            config = _config_test(boveda_dir)
            resultado = cmd_cifrar("/tmp/no_existe_xyz.txt", config)
            self.assertEqual(resultado, 1)

    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_cifrar_archivo_ya_gpg(self, mock_pass):
        with tempfile.TemporaryDirectory() as boveda_dir:
            config = _config_test(boveda_dir)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".gpg") as f:
                archivo = f.name
            try:
                resultado = cmd_cifrar(archivo, config)
                self.assertEqual(resultado, 1)
            finally:
                Path(archivo).unlink(missing_ok=True)


class TestCmdDescifrar(TestCase):
    @patch("guardian_secretos.notificar")
    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_descifrar_desde_boveda(self, mock_pass, mock_notif):
        with tempfile.TemporaryDirectory() as boveda_dir:
            # Crear archivo .gpg en la boveda
            gpg_file = Path(boveda_dir) / "secreto.gpg"
            gpg_file.write_bytes(b"datos cifrados")

            def fake_descifrar(ruta_gpg, ruta_destino, contrasena):
                ruta_destino.write_bytes(b"datos descifrados")
                return True

            config = _config_test(boveda_dir)

            # Cambiar al directorio temporal para la salida
            with tempfile.TemporaryDirectory() as salida_dir:
                original_cwd = os.getcwd()
                try:
                    os.chdir(salida_dir)
                    with patch("guardian_secretos._descifrar_gpg", side_effect=fake_descifrar):
                        resultado = cmd_descifrar("secreto", config)
                    self.assertEqual(resultado, 0)
                finally:
                    os.chdir(original_cwd)

    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_descifrar_no_existe(self, mock_pass):
        with tempfile.TemporaryDirectory() as boveda_dir:
            config = _config_test(boveda_dir)
            resultado = cmd_descifrar("fantasma", config)
            self.assertEqual(resultado, 1)


class TestCmdAbrir(TestCase):
    @patch("guardian_secretos.notificar")
    @patch("guardian_secretos._shred_archivo", return_value=True)
    @patch("guardian_secretos._descifrar_gpg")
    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_abrir_ok(self, mock_pass, mock_descifrar, mock_shred, mock_notif):
        with tempfile.TemporaryDirectory() as boveda_dir:
            # Crear archivo .gpg en la boveda
            gpg_file = Path(boveda_dir) / "notas.gpg"
            gpg_file.write_bytes(b"datos cifrados")

            def side_descifrar(ruta_gpg, ruta_tmp, contrasena):
                # Simular que GPG escribe el descifrado en el temporal
                ruta_tmp.write_text("contenido secreto")
                return True

            mock_descifrar.side_effect = side_descifrar
            config = _config_test(boveda_dir)

            resultado = cmd_abrir("notas", config)
            self.assertEqual(resultado, 0)
            mock_shred.assert_called_once()

    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_abrir_no_existe(self, mock_pass):
        with tempfile.TemporaryDirectory() as boveda_dir:
            config = _config_test(boveda_dir)
            resultado = cmd_abrir("fantasma", config)
            self.assertEqual(resultado, 1)

    @patch("guardian_secretos._shred_archivo", return_value=True)
    @patch("guardian_secretos._descifrar_gpg", return_value=False)
    @patch("guardian_secretos._pedir_contrasena", return_value="mala")
    def test_abrir_contrasena_incorrecta(self, mock_pass, mock_descifrar, mock_shred):
        with tempfile.TemporaryDirectory() as boveda_dir:
            gpg_file = Path(boveda_dir) / "notas.gpg"
            gpg_file.write_bytes(b"datos cifrados")
            config = _config_test(boveda_dir)

            resultado = cmd_abrir("notas", config)
            self.assertEqual(resultado, 1)
            # El temporal se limpia incluso en error
            mock_shred.assert_called_once()


class TestCmdEditar(TestCase):
    @patch("guardian_secretos.notificar")
    @patch("guardian_secretos._shred_archivo", return_value=True)
    @patch("guardian_secretos.subprocess.run")
    @patch("guardian_secretos._pedir_contrasena", return_value="clave123")
    def test_editar_ok(self, mock_pass, mock_subrun, mock_shred, mock_notif):
        with tempfile.TemporaryDirectory() as boveda_dir:
            gpg_file = Path(boveda_dir) / "config.gpg"
            gpg_file.write_bytes(b"datos cifrados")

            def fake_descifrar(ruta_gpg, ruta_tmp, contrasena):
                ruta_tmp.write_text("contenido editable")
                return True

            def fake_cifrar(origen, destino, contrasena, cipher):
                destino.write_bytes(b"re-cifrado")
                return True

            # Editor "cat" sale con 0
            mock_subrun.return_value = MagicMock(returncode=0)

            config = _config_test(boveda_dir)
            with patch("guardian_secretos._descifrar_gpg", side_effect=fake_descifrar), \
                 patch("guardian_secretos._cifrar_gpg", side_effect=fake_cifrar):
                resultado = cmd_editar("config", config)

            self.assertEqual(resultado, 0)
            mock_shred.assert_called()
            mock_notif.assert_called()

    @patch("guardian_secretos._shred_archivo", return_value=True)
    @patch("guardian_secretos._descifrar_gpg", return_value=False)
    @patch("guardian_secretos._pedir_contrasena", return_value="mala")
    def test_editar_contrasena_incorrecta(self, mock_pass, mock_descifrar, mock_shred):
        with tempfile.TemporaryDirectory() as boveda_dir:
            gpg_file = Path(boveda_dir) / "config.gpg"
            gpg_file.write_bytes(b"datos cifrados")
            config = _config_test(boveda_dir)

            resultado = cmd_editar("config", config)
            self.assertEqual(resultado, 1)
            mock_shred.assert_called()


class TestCmdEstado(TestCase):
    def test_boveda_vacia(self):
        with tempfile.TemporaryDirectory() as boveda_dir:
            config = _config_test(boveda_dir)
            resultado = cmd_estado(config)
            self.assertEqual(resultado, 0)

    def test_boveda_con_archivos(self):
        with tempfile.TemporaryDirectory() as boveda_dir:
            # Crear archivos .gpg de prueba
            (Path(boveda_dir) / "passwords.gpg").write_bytes(b"x" * 100)
            (Path(boveda_dir) / "apis.gpg").write_bytes(b"y" * 200)

            config = _config_test(boveda_dir)
            resultado = cmd_estado(config)
            self.assertEqual(resultado, 0)


# ── R6-007: _pedir_contrasena sin TTY → mensaje amigable, exit≠0 ─────────────

class TestR6007PedirContrasena(TestCase):
    """R6-007: sin stdin interactivo → exit≠0 limpio (sin traceback)."""

    def test_sin_tty_sale_nonzero(self):
        """sys.stdin.isatty() == False → sys.exit(1) con mensaje amigable."""
        from guardian_secretos import _pedir_contrasena
        import io
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                with self.assertRaises(SystemExit) as ctx:
                    _pedir_contrasena()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIn("no interactivo", mock_err.getvalue().lower())

    def test_sin_tty_no_hay_traceback(self):
        """Sin TTY no se propaga EOFError (sin traceback)."""
        from guardian_secretos import _pedir_contrasena
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with patch("sys.stderr"):
                try:
                    _pedir_contrasena()
                except SystemExit:
                    pass  # Esperado
                except EOFError:
                    self.fail("EOFError no debería propagarse (causaría traceback)")

    def test_contrasena_vacia_sigue_dando_valueerror(self):
        """Contraseña vacía (stdin es TTY pero usuario da enter) → ValueError, no exit 0."""
        from guardian_secretos import _pedir_contrasena
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("getpass.getpass", return_value=""):
                with self.assertRaises(ValueError) as ctx:
                    _pedir_contrasena()
        self.assertIn("vacia", str(ctx.exception).lower())

    def test_con_tty_y_contrasena_valida_funciona(self):
        """stdin es TTY y contraseña no vacía → devuelve la contraseña (camino feliz)."""
        from guardian_secretos import _pedir_contrasena
        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch("getpass.getpass", return_value="mi_contrasena_segura"):
                resultado = _pedir_contrasena()
        self.assertEqual(resultado, "mi_contrasena_segura")


if __name__ == "__main__":
    main()
