#!/usr/bin/env python3
"""Tests para el_arquitecto_del_castillo."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from el_arquitecto_del_castillo import (
    COMANDOS,
    construir_comando,
    generar_ayuda,
    generar_splash,
    match_por_keyword,
    match_por_url,
)


class TestMatchPorKeyword(unittest.TestCase):
    """Tests para la deteccion de comandos por keyword."""

    def test_match_error(self):
        """Detecta 'error' como comando errores."""
        resultado = match_por_keyword("hay errores?")
        self.assertEqual(resultado, "errores")

    def test_match_seguridad(self):
        """Detecta 'seguridad' como comando secretos."""
        resultado = match_por_keyword("quiero escanear la seguridad")
        self.assertEqual(resultado, "secretos")

    def test_match_duplicados(self):
        """Detecta 'duplicado' como comando dupes."""
        resultado = match_por_keyword("busca archivos duplicados")
        self.assertEqual(resultado, "dupes")

    def test_match_dashboard(self):
        """Detecta 'dashboard' como comando castillo."""
        resultado = match_por_keyword("muestra el dashboard")
        self.assertEqual(resultado, "castillo")

    def test_match_feeds(self):
        """Detecta 'noticias' como comando feeds."""
        resultado = match_por_keyword("quiero ver las noticias")
        self.assertEqual(resultado, "feeds")

    def test_sin_match(self):
        """Devuelve None cuando no hay match."""
        resultado = match_por_keyword("hola que tal como estas")
        self.assertIsNone(resultado)

    def test_entrada_vacia(self):
        """Devuelve None con entrada vacia."""
        resultado = match_por_keyword("")
        self.assertIsNone(resultado)

    def test_solo_espacios(self):
        """Devuelve None con solo espacios."""
        resultado = match_por_keyword("   ")
        self.assertIsNone(resultado)


class TestMatchPorUrl(unittest.TestCase):
    """Tests para la deteccion de URLs de media."""

    def test_youtube_url(self):
        """Detecta URL de YouTube."""
        resultado = match_por_url("descarga https://youtube.com/watch?v=abc123")
        self.assertEqual(resultado, "yt https://youtube.com/watch?v=abc123")

    def test_youtu_be_url(self):
        """Detecta URL corta de YouTube."""
        resultado = match_por_url("baja https://youtu.be/abc123")
        self.assertEqual(resultado, "yt https://youtu.be/abc123")

    def test_soundcloud_url(self):
        """Detecta URL de SoundCloud."""
        resultado = match_por_url("descarga https://soundcloud.com/artist/track")
        self.assertEqual(resultado, "yt https://soundcloud.com/artist/track")

    def test_sin_url(self):
        """Devuelve None sin URL de media."""
        resultado = match_por_url("quiero descargar algo")
        self.assertIsNone(resultado)

    def test_url_otra(self):
        """Devuelve None con URL no-media."""
        resultado = match_por_url("abre https://google.com")
        self.assertIsNone(resultado)


class TestConstruirComando(unittest.TestCase):
    """Tests para la construccion de comandos con argumentos."""

    def test_comando_simple(self):
        """Comando sin argumentos extra."""
        resultado = construir_comando("errores", "hay errores?")
        self.assertEqual(resultado, "errores")

    def test_comando_con_args_fijos(self):
        """Comando que ya tiene argumentos fijos no recibe mas."""
        resultado = construir_comando("secretos", "quiero escanear amenazas")
        self.assertEqual(resultado, "secretos --amenazas")

    def test_comando_con_args_usuario(self):
        """Comando recibe argumentos del input del usuario."""
        resultado = construir_comando("trad", "traduce 'hello world'")
        # Debe incluir argumentos relevantes
        self.assertTrue(resultado.startswith("trad"))

    def test_comando_desconocido(self):
        """Comando no registrado devuelve el nombre tal cual."""
        resultado = construir_comando("inexistente", "algo")
        self.assertEqual(resultado, "inexistente")


class TestGenerarSplash(unittest.TestCase):
    """Tests para la pantalla de bienvenida."""

    @patch("el_arquitecto_del_castillo._contar_automatizaciones", return_value=10)
    @patch("el_arquitecto_del_castillo._contar_servicios", return_value=3)
    def test_splash_no_crash(self, mock_srv, mock_auto):
        """El splash se genera sin errores."""
        resultado = generar_splash()
        self.assertIsInstance(resultado, str)
        self.assertIn("ARQUITECTO", resultado)

    @patch("el_arquitecto_del_castillo._contar_automatizaciones", return_value=10)
    @patch("el_arquitecto_del_castillo._contar_servicios", return_value=3)
    def test_splash_contiene_box(self, mock_srv, mock_auto):
        """El splash contiene caracteres de box drawing."""
        resultado = generar_splash()
        self.assertIn("┌", resultado)
        self.assertIn("┘", resultado)

    @patch("el_arquitecto_del_castillo._contar_automatizaciones", return_value=25)
    @patch("el_arquitecto_del_castillo._contar_servicios", return_value=9)
    def test_splash_muestra_contadores(self, mock_srv, mock_auto):
        """El splash muestra los contadores de automatizaciones y servicios."""
        resultado = generar_splash()
        self.assertIn("25", resultado)
        self.assertIn("9", resultado)


class TestGenerarAyuda(unittest.TestCase):
    """Tests para el texto de ayuda."""

    def test_ayuda_lista_todos_los_comandos(self):
        """La ayuda contiene todos los comandos registrados."""
        resultado = generar_ayuda()
        for nombre in COMANDOS:
            self.assertIn(nombre, resultado, f"Falta comando '{nombre}' en la ayuda")

    def test_ayuda_lista_comandos_repl(self):
        """La ayuda contiene los comandos especiales del REPL."""
        resultado = generar_ayuda()
        self.assertIn("ayuda", resultado)
        self.assertIn("limpiar", resultado)
        self.assertIn("salir", resultado)


class TestConfirmadorTerminal(unittest.TestCase):
    """Tests para confirmador_terminal (ERR-001)."""

    def _importar_confirmador(self):
        import importlib
        import sys as _sys
        ruta = str(Path(__file__).resolve().parent.parent / "arquitecto")
        if ruta not in _sys.path:
            _sys.path.insert(0, ruta)
        import repl as _repl
        importlib.reload(_repl)
        return _repl.confirmador_terminal

    def test_stdin_no_tty_cancela(self):
        """Con stdin no-TTY, confirmador devuelve False sin leer."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=False):
            resultado = confirmador("¿Borrar todo?")
        self.assertFalse(resultado)

    def test_salir_cancela(self):
        """'salir' no es afirmativo; debe devolver False."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="salir"):
                resultado = confirmador("¿Ejecutar?")
        self.assertFalse(resultado)

    def test_afirmativo_s(self):
        """'s' devuelve True."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="s"):
                resultado = confirmador("¿Ejecutar?")
        self.assertTrue(resultado)

    def test_afirmativo_si(self):
        """'si' devuelve True."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="SI"):
                resultado = confirmador("¿Ejecutar?")
        self.assertTrue(resultado)

    def test_afirmativo_yes(self):
        """'yes' devuelve True."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="yes"):
                resultado = confirmador("¿Ejecutar?")
        self.assertTrue(resultado)

    def test_vacio_cancela(self):
        """Respuesta vacía devuelve False."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value=""):
                resultado = confirmador("¿Ejecutar?")
        self.assertFalse(resultado)

    def test_prefijo_s_no_basta(self):
        """'salida' (empieza por 's') devuelve False — no basta el prefijo."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", return_value="salida"):
                resultado = confirmador("¿Ejecutar?")
        self.assertFalse(resultado)

    def test_eof_cancela(self):
        """EOFError devuelve False."""
        confirmador = self._importar_confirmador()
        with patch("sys.stdin.isatty", return_value=True):
            with patch("builtins.input", side_effect=EOFError):
                resultado = confirmador("¿Ejecutar?")
        self.assertFalse(resultado)


if __name__ == "__main__":
    unittest.main()
