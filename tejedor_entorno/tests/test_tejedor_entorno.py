#!/usr/bin/env python3
"""Tests para tejedor_entorno."""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tejedor_entorno.tejedor_entorno import (
    franja_actual,
    obtener_wallpapers,
    aplicar_wallpaper,
    main as tejedor_main,
)


class TestFranjaActual(TestCase):
    def test_manana(self):
        for h in (6, 7, 10, 11):
            self.assertEqual(franja_actual(h), "manana")

    def test_dia(self):
        for h in (12, 13, 15, 17):
            self.assertEqual(franja_actual(h), "dia")

    def test_tarde(self):
        for h in (18, 19, 20):
            self.assertEqual(franja_actual(h), "tarde")

    def test_noche(self):
        for h in (21, 23, 0, 3, 5):
            self.assertEqual(franja_actual(h), "noche")


class TestObtenerWallpapers(TestCase):
    def test_carpeta_no_existe(self):
        self.assertEqual(obtener_wallpapers(Path("/tmp/no_existe_xyz")), [])

    def test_filtra_extensiones(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "foto.jpg").touch()
            (p / "foto.png").touch()
            (p / "nota.txt").touch()
            (p / "datos.csv").touch()
            resultado = obtener_wallpapers(p)
            self.assertEqual(len(resultado), 2)


class TestAplicarWallpaper(TestCase):
    @patch("tejedor_entorno.tejedor_entorno.subprocess.Popen")
    @patch("tejedor_entorno.tejedor_entorno.subprocess.run")
    def test_aplicar_exitoso(self, mock_run, mock_popen):
        import tempfile
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_popen.return_value = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            self.assertTrue(aplicar_wallpaper(Path(f.name)))

    @patch("tejedor_entorno.tejedor_entorno.subprocess.run")
    def test_aplicar_fallo(self, mock_run):
        mock_run.side_effect = Exception("error de prueba")
        self.assertFalse(aplicar_wallpaper(Path("/tmp/foto.jpg")))

    @patch("tejedor_entorno.tejedor_entorno.subprocess.run")
    def test_omarchy_no_encontrado(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        self.assertFalse(aplicar_wallpaper(Path("/tmp/foto.jpg")))


class TestTejedorHelp(TestCase):
    """N-002: --help no cambia el wallpaper; sin flags sí aplica wallpaper."""

    def test_help_no_llama_aplicar(self):
        """Con --help argparse imprime y sale sin llamar a aplicar_wallpaper."""
        with patch("tejedor_entorno.tejedor_entorno.aplicar_wallpaper") as mock_ap:
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.argv", ["tejedor_entorno", "--help"]):
                    tejedor_main()
        mock_ap.assert_not_called()
        self.assertEqual(ctx.exception.code, 0)

    @patch("tejedor_entorno.tejedor_entorno.aplicar_wallpaper", return_value=True)
    @patch("tejedor_entorno.tejedor_entorno.obtener_wallpapers")
    @patch("tejedor_entorno.tejedor_entorno.cargar_config", return_value={})
    def test_sin_flags_aplica_wallpaper(self, _cfg, mock_wps, mock_ap):
        """Sin flags (comportamiento del timer), aplicar_wallpaper es llamado."""
        mock_wps.return_value = [Path("/tmp/fake.jpg")]
        with patch("sys.argv", ["tejedor_entorno"]):
            tejedor_main()
        mock_ap.assert_called_once()


if __name__ == "__main__":
    main()
