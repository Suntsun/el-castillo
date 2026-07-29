#!/usr/bin/env python3
"""Tests para traductor_terminal."""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from traductor_terminal.traductor_terminal import (
    verificar_trans,
    traducir,
    leer_portapapeles,
    copiar_portapapeles,
    detectar_idioma,
)


class TestVerificarTrans(TestCase):
    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_trans_disponible(self, mock_which):
        mock_which.return_value = "/usr/bin/trans"
        self.assertTrue(verificar_trans())

    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_trans_no_disponible(self, mock_which):
        mock_which.return_value = None
        self.assertFalse(verificar_trans())


class TestTraducir(TestCase):
    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_traduccion_exitosa(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="hola mundo\n",
            stderr="",
        )
        resultado = traducir("hello world", "es")
        self.assertEqual(resultado, "hola mundo")
        mock_run.assert_called_once()

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_traduccion_fallo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error de red",
        )
        resultado = traducir("hello", "es")
        self.assertIsNone(resultado)

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_traduccion_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="trans", timeout=10)
        resultado = traducir("hello", "es", timeout=10)
        self.assertIsNone(resultado)

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_traduccion_resultado_vacio(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        resultado = traducir("hello", "es")
        self.assertIsNone(resultado)

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_traduccion_idioma_destino(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="bonjour\n",
            stderr="",
        )
        traducir("hello", "fr", motor="google")
        args_llamada = mock_run.call_args[0][0]
        self.assertIn(":fr", args_llamada)
        self.assertIn("google", args_llamada)


class TestPortapapeles(TestCase):
    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_leer_sin_wl_paste(self, mock_which):
        mock_which.return_value = None
        resultado = leer_portapapeles()
        self.assertIsNone(resultado)

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_leer_portapapeles_ok(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/wl-paste"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="texto copiado",
            stderr="",
        )
        resultado = leer_portapapeles()
        self.assertEqual(resultado, "texto copiado")

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_leer_portapapeles_vacio(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/wl-paste"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="  \n",
            stderr="",
        )
        resultado = leer_portapapeles()
        self.assertIsNone(resultado)

    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_copiar_sin_wl_copy(self, mock_which):
        mock_which.return_value = None
        resultado = copiar_portapapeles("texto")
        self.assertFalse(resultado)

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    @patch("traductor_terminal.traductor_terminal.shutil.which")
    def test_copiar_portapapeles_ok(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/wl-copy"
        mock_run.return_value = MagicMock(returncode=0)
        resultado = copiar_portapapeles("texto traducido")
        self.assertTrue(resultado)


class TestDetectarIdioma(TestCase):
    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_detectar_ok(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="English\n",
            stderr="",
        )
        resultado = detectar_idioma("hello world")
        self.assertEqual(resultado, "English")

    @patch("traductor_terminal.traductor_terminal.subprocess.run")
    def test_detectar_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="trans", timeout=10)
        resultado = detectar_idioma("hello")
        self.assertIsNone(resultado)


class TestValidacionIdioma(TestCase):
    """ERR-007: lista blanca de idiomas — inválido sale con error; válido funciona."""

    @patch("traductor_terminal.traductor_terminal.shutil.which", return_value="/usr/bin/trans")
    @patch("traductor_terminal.traductor_terminal.cargar_config", return_value={})
    def test_idioma_invalido_sale_con_error(self, _cfg, _which):
        with patch("sys.argv", ["trad", "hello", "--to", "IDIOMA_INEXISTENTE"]):
            with self.assertRaises(SystemExit) as ctx:
                from traductor_terminal.traductor_terminal import main as trad_main
                trad_main()
        self.assertNotEqual(ctx.exception.code, 0)

    @patch("traductor_terminal.traductor_terminal.traducir", return_value="hola")
    @patch("traductor_terminal.traductor_terminal.shutil.which", return_value="/usr/bin/trans")
    @patch("traductor_terminal.traductor_terminal.cargar_config", return_value={})
    def test_idioma_valido_funciona(self, _cfg, _which, _trad):
        with patch("sys.argv", ["trad", "hello", "--to", "es", "--silent"]):
            from traductor_terminal.traductor_terminal import main as trad_main
            trad_main()  # No debe lanzar excepción


if __name__ == "__main__":
    main()
