#!/usr/bin/env python3
"""Tests para guardian_credenciales."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian_credenciales import (
    enmascarar_key,
    copiar_key,
    ver_key,
    listar_apis,
    anadir_api,
    borrar_api,
    verificar_apis,
    _verificar_una_api,
    _ruta_apis,
    _escribir_toml_api,
)


class TestEnmascarKey(TestCase):
    def test_key_larga(self):
        resultado = enmascarar_key("sk-proj-abcdefghijklmnop")
        self.assertEqual(resultado, "sk-p...mnop")

    def test_key_corta(self):
        resultado = enmascarar_key("1234")
        self.assertEqual(resultado, "****")

    def test_key_exacta_8(self):
        resultado = enmascarar_key("12345678")
        self.assertEqual(resultado, "****")

    def test_key_9_chars(self):
        resultado = enmascarar_key("123456789")
        self.assertEqual(resultado, "1234...6789")


class TestCopiarKey(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {"almacenamiento": {"ruta": self.tmpdir}}
        # Crear un archivo TOML de prueba
        api_file = Path(self.tmpdir) / "openai.toml"
        api_file.write_text(
            '[api]\n'
            'key = "sk-proj-test123456789xyz"\n'
            'descripcion = "OpenAI Test"\n'
            'fecha_alta = "2026-05-27"\n'
        )

    @patch("guardian_credenciales.notificar")
    @patch("guardian_credenciales.subprocess.run")
    def test_copiar_ok(self, mock_run, mock_notif):
        mock_run.return_value = MagicMock(returncode=0)
        resultado = copiar_key("openai", self.config)
        self.assertEqual(resultado, 0)
        mock_run.assert_called_once()
        # Verificar que wl-copy se llamo con la key
        args_llamada = mock_run.call_args
        self.assertEqual(args_llamada[0][0][:2], ["wl-copy", "--"])
        self.assertIn("sk-proj-test123456789xyz", args_llamada[0][0])
        # Verificar notificacion
        mock_notif.assert_called_once()

    @patch("guardian_credenciales.notificar")
    @patch("guardian_credenciales.subprocess.run")
    def test_copiar_api_no_existe(self, mock_run, mock_notif):
        resultado = copiar_key("inexistente", self.config)
        self.assertEqual(resultado, 1)
        mock_run.assert_not_called()
        mock_notif.assert_not_called()

    @patch("guardian_credenciales.notificar")
    @patch("guardian_credenciales.subprocess.run")
    def test_copiar_wl_copy_falla(self, mock_run, mock_notif):
        mock_run.return_value = MagicMock(returncode=1)
        resultado = copiar_key("openai", self.config)
        self.assertEqual(resultado, 1)
        mock_notif.assert_not_called()

    @patch("guardian_credenciales.notificar")
    @patch("guardian_credenciales.subprocess.run")
    def test_copiar_wl_copy_no_encontrado(self, mock_run, mock_notif):
        mock_run.side_effect = FileNotFoundError()
        resultado = copiar_key("openai", self.config)
        self.assertEqual(resultado, 1)


class TestVerKey(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = {"almacenamiento": {"ruta": self.tmpdir}}
        api_file = Path(self.tmpdir) / "github.toml"
        api_file.write_text(
            '[api]\n'
            'key = "ghp_abcdef123456"\n'
            'descripcion = "GitHub Token"\n'
            'fecha_alta = "2026-05-27"\n'
        )

    def test_ver_ok(self):
        resultado = ver_key("github", self.config)
        self.assertEqual(resultado, 0)

    def test_ver_no_existe(self):
        resultado = ver_key("inexistente", self.config)
        self.assertEqual(resultado, 1)


class TestListarApis(TestCase):
    def test_listar_vacio(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            resultado = listar_apis(config)
            self.assertEqual(resultado, 0)

    def test_listar_con_apis(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            (Path(d) / "openai.toml").write_text(
                '[api]\nkey = "sk-test"\ndescripcion = "OpenAI"\n'
            )
            (Path(d) / "github.toml").write_text(
                '[api]\nkey = "ghp-test"\ndescripcion = "GitHub"\n'
            )
            resultado = listar_apis(config)
            self.assertEqual(resultado, 0)


class TestAnadirApi(TestCase):
    @patch("guardian_credenciales.notificar")
    @patch("builtins.input", return_value="Mi API de prueba")
    @patch("guardian_credenciales.getpass.getpass", return_value="mi-key-secreta-larga")
    def test_anadir_nueva(self, mock_getpass, mock_input, mock_notif):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            resultado = anadir_api("nueva_api", config)
            self.assertEqual(resultado, 0)
            # Verificar que el archivo se creo
            archivo = Path(d) / "nueva_api.toml"
            self.assertTrue(archivo.exists())
            contenido = archivo.read_text()
            self.assertIn("mi-key-secreta-larga", contenido)
            self.assertIn("Mi API de prueba", contenido)

    @patch("guardian_credenciales.getpass.getpass", return_value="")
    def test_anadir_key_vacia(self, mock_getpass):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            resultado = anadir_api("vacia", config)
            self.assertEqual(resultado, 1)

    @patch("guardian_credenciales.notificar")
    @patch("builtins.input", side_effect=["s", "Desc nueva"])
    @patch("guardian_credenciales.getpass.getpass", return_value="nueva-key-larga")
    def test_anadir_sobreescribir(self, mock_getpass, mock_input, mock_notif):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            # Crear API existente
            (Path(d) / "existente.toml").write_text(
                '[api]\nkey = "vieja"\ndescripcion = "Vieja"\n'
            )
            resultado = anadir_api("existente", config)
            self.assertEqual(resultado, 0)
            contenido = (Path(d) / "existente.toml").read_text()
            self.assertIn("nueva-key-larga", contenido)


class TestBorrarApi(TestCase):
    @patch("guardian_credenciales.notificar")
    @patch("builtins.input", return_value="s")
    def test_borrar_ok(self, mock_input, mock_notif):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            archivo = Path(d) / "borrable.toml"
            archivo.write_text('[api]\nkey = "test"\n')
            resultado = borrar_api("borrable", config)
            self.assertEqual(resultado, 0)
            self.assertFalse(archivo.exists())

    @patch("builtins.input", return_value="n")
    def test_borrar_cancelado(self, mock_input):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            archivo = Path(d) / "conservar.toml"
            archivo.write_text('[api]\nkey = "test"\n')
            resultado = borrar_api("conservar", config)
            self.assertEqual(resultado, 0)
            self.assertTrue(archivo.exists())

    def test_borrar_no_existe(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            resultado = borrar_api("fantasma", config)
            self.assertEqual(resultado, 1)


class TestVerificarApis(TestCase):
    @patch("guardian_credenciales._verificar_una_api")
    def test_verificar_ok(self, mock_verif):
        mock_verif.return_value = True
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            (Path(d) / "openai.toml").write_text(
                '[api]\nkey = "sk-test"\ndescripcion = "OpenAI"\n\n'
                '[verificacion]\nurl = "https://api.openai.com/v1/models"\n'
                'header = "Authorization: Bearer {key}"\ncodigo_ok = 200\n'
            )
            resultado = verificar_apis(config)
            self.assertEqual(resultado, 0)
            mock_verif.assert_called_once()

    @patch("guardian_credenciales.notificar")
    @patch("guardian_credenciales._verificar_una_api")
    def test_verificar_fallo(self, mock_verif, mock_notif):
        mock_verif.return_value = False
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}, "notificacion": {"duracion": 3000}}
            (Path(d) / "rota.toml").write_text(
                '[api]\nkey = "bad-key"\ndescripcion = "API Rota"\n\n'
                '[verificacion]\nurl = "https://example.com/check"\n'
                'header = "X-Api-Key: {key}"\ncodigo_ok = 200\n'
            )
            resultado = verificar_apis(config)
            self.assertEqual(resultado, 1)
            mock_notif.assert_called_once()
            # Verificar que la notificacion indica fallo
            args_notif = mock_notif.call_args
            self.assertIn("problemas", args_notif[0][1])

    def test_verificar_sin_apis(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            resultado = verificar_apis(config)
            self.assertEqual(resultado, 0)

    def test_verificar_sin_seccion_verificacion(self):
        """APIs sin seccion [verificacion] se ignoran."""
        with tempfile.TemporaryDirectory() as d:
            config = {"almacenamiento": {"ruta": d}}
            (Path(d) / "simple.toml").write_text(
                '[api]\nkey = "test"\ndescripcion = "Simple"\n'
            )
            resultado = verificar_apis(config)
            self.assertEqual(resultado, 0)


class TestVerificarUnaApi(TestCase):
    @patch("guardian_credenciales.urllib.request.urlopen")
    def test_respuesta_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        resultado = _verificar_una_api(
            "https://api.example.com/check",
            "Authorization: Bearer {key}",
            "mi-key",
            200,
        )
        self.assertTrue(resultado)

    @patch("guardian_credenciales.urllib.request.urlopen")
    def test_respuesta_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        resultado = _verificar_una_api(
            "https://api.example.com/check",
            "Authorization: Bearer {key}",
            "mi-key",
            200,
        )
        self.assertFalse(resultado)

    @patch("guardian_credenciales.urllib.request.urlopen")
    def test_http_error_con_codigo_esperado(self, mock_urlopen):
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            "https://api.example.com", 401, "Unauthorized", {}, None
        )
        # Si el codigo_ok es 401 (raro pero posible), deberia retornar True
        resultado = _verificar_una_api(
            "https://api.example.com/check",
            "Authorization: Bearer {key}",
            "mi-key",
            401,
        )
        self.assertTrue(resultado)


class TestEscribirToml(TestCase):
    def test_formato_correcto(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "test.toml"
            _escribir_toml_api(ruta, "mi-key-secreta", "Mi API")
            contenido = ruta.read_text()
            self.assertIn('[api]', contenido)
            self.assertIn('key = "mi-key-secreta"', contenido)
            self.assertIn('descripcion = "Mi API"', contenido)
            self.assertIn('fecha_alta =', contenido)

    def test_permisos_600(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "test.toml"
            _escribir_toml_api(ruta, "key", "desc")
            permisos = oct(ruta.stat().st_mode)[-3:]
            self.assertEqual(permisos, "600")


class TestRutaApis(TestCase):
    def test_crea_directorio_si_no_existe(self):
        with tempfile.TemporaryDirectory() as d:
            ruta_nueva = Path(d) / "subdir" / "apis"
            config = {"almacenamiento": {"ruta": str(ruta_nueva)}}
            resultado = _ruta_apis(config)
            self.assertTrue(resultado.exists())
            self.assertTrue(resultado.is_dir())

    def test_permisos_700(self):
        with tempfile.TemporaryDirectory() as d:
            ruta_nueva = Path(d) / "nueva"
            config = {"almacenamiento": {"ruta": str(ruta_nueva)}}
            resultado = _ruta_apis(config)
            permisos = oct(resultado.stat().st_mode)[-3:]
            self.assertEqual(permisos, "700")


if __name__ == "__main__":
    main()
