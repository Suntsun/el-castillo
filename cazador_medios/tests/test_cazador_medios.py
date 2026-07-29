#!/usr/bin/env python3
"""Tests para cazador_medios."""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from cazador_medios.cazador_medios import (
    detectar_plataforma,
    verificar_dependencias,
    resolver_ruta_destino,
    obtener_formato,
    construir_comando,
    descargar,
)


class TestDetectarPlataforma(TestCase):
    def test_youtube_watch(self):
        self.assertEqual(
            detectar_plataforma("https://www.youtube.com/watch?v=abc123"),
            "youtube",
        )

    def test_youtube_short(self):
        self.assertEqual(
            detectar_plataforma("https://youtu.be/abc123"),
            "youtube",
        )

    def test_soundcloud(self):
        self.assertEqual(
            detectar_plataforma("https://soundcloud.com/artist/track"),
            "soundcloud",
        )

    def test_otro(self):
        self.assertEqual(
            detectar_plataforma("https://vimeo.com/123456"),
            "otro",
        )

    def test_youtube_mayusculas(self):
        self.assertEqual(
            detectar_plataforma("https://WWW.YOUTUBE.COM/watch?v=abc"),
            "youtube",
        )


class TestVerificarDependencias(TestCase):
    @patch("cazador_medios.cazador_medios.shutil.which")
    def test_todas_instaladas(self, mock_which):
        mock_which.return_value = "/usr/bin/algo"
        faltantes = verificar_dependencias()
        self.assertEqual(faltantes, [])

    @patch("cazador_medios.cazador_medios.shutil.which")
    def test_falta_ytdlp(self, mock_which):
        def side_effect(cmd):
            return None if cmd == "yt-dlp" else "/usr/bin/ffmpeg"
        mock_which.side_effect = side_effect
        faltantes = verificar_dependencias()
        self.assertIn("yt-dlp", faltantes)

    @patch("cazador_medios.cazador_medios.shutil.which")
    def test_falta_ffmpeg(self, mock_which):
        def side_effect(cmd):
            return "/usr/bin/yt-dlp" if cmd == "yt-dlp" else None
        mock_which.side_effect = side_effect
        faltantes = verificar_dependencias()
        self.assertIn("ffmpeg", faltantes)

    @patch("cazador_medios.cazador_medios.shutil.which")
    def test_faltan_ambas(self, mock_which):
        mock_which.return_value = None
        faltantes = verificar_dependencias()
        self.assertEqual(len(faltantes), 2)


class TestResolverRutaDestino(TestCase):
    def test_youtube(self):
        ruta = resolver_ruta_destino("youtube", {})
        self.assertTrue(str(ruta).endswith("Música/YouTube"))

    def test_soundcloud(self):
        ruta = resolver_ruta_destino("soundcloud", {})
        self.assertTrue(str(ruta).endswith("Música/SoundCloud"))

    def test_otro_va_a_youtube(self):
        ruta = resolver_ruta_destino("otro", {})
        self.assertTrue(str(ruta).endswith("Música/YouTube"))

    def test_rutas_custom(self):
        rutas = {"youtube": "~/Descargas/Música"}
        ruta = resolver_ruta_destino("youtube", rutas)
        self.assertTrue(str(ruta).endswith("Descargas/Música"))


class TestObtenerFormato(TestCase):
    def test_formato_bestaudio(self):
        fmt = obtener_formato()
        self.assertIn("bestaudio", fmt)


class TestConstruirComando(TestCase):
    def test_comando_mp3(self):
        cmd = construir_comando(
            "https://youtube.com/watch?v=abc",
            Path("/tmp/test"),
            "bestaudio/best",
            es_playlist=False,
        )
        self.assertEqual(cmd[0], "yt-dlp")
        self.assertIn("-x", cmd)
        self.assertIn("--audio-format", cmd)
        self.assertIn("mp3", cmd)
        self.assertIn("--no-playlist", cmd)

    def test_comando_playlist(self):
        cmd = construir_comando(
            "https://youtube.com/playlist?list=abc",
            Path("/tmp/test"),
            "bestaudio/best",
            es_playlist=True,
        )
        self.assertNotIn("--no-playlist", cmd)

    def test_url_incluida(self):
        url = "https://youtube.com/watch?v=test123"
        cmd = construir_comando(url, Path("/tmp"), "bestaudio/best", False)
        self.assertEqual(cmd[-1], url)

    def test_calidad_maxima(self):
        cmd = construir_comando(
            "https://youtube.com/watch?v=abc",
            Path("/tmp/test"),
            "bestaudio/best",
            es_playlist=False,
        )
        self.assertIn("--audio-quality", cmd)
        self.assertIn("0", cmd)


class TestDescargar(TestCase):
    @patch("cazador_medios.cazador_medios.subprocess.run")
    def test_descarga_exitosa(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("pathlib.Path.mkdir"):
            resultado = descargar(
                "https://youtube.com/watch?v=abc",
                Path("/tmp/test"),
                "bestaudio/best",
                es_playlist=False,
            )
        self.assertTrue(resultado)

    @patch("cazador_medios.cazador_medios.subprocess.run")
    def test_descarga_fallida(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        with patch("pathlib.Path.mkdir"):
            resultado = descargar(
                "https://youtube.com/watch?v=abc",
                Path("/tmp/test"),
                "bestaudio/best",
                es_playlist=False,
            )
        self.assertFalse(resultado)

    @patch("cazador_medios.cazador_medios.subprocess.run")
    def test_ytdlp_no_encontrado(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        with patch("pathlib.Path.mkdir"):
            resultado = descargar(
                "https://youtube.com/watch?v=abc",
                Path("/tmp/test"),
                "bestaudio/best",
                es_playlist=False,
            )
        self.assertFalse(resultado)


if __name__ == "__main__":
    main()
