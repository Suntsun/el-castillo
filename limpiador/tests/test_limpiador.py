#!/usr/bin/env python3
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import limpiador.limpiador as mod
from limpiador.limpiador import (
    _formato_espacio,
    _contar_archivos_antiguos,
    _limpiar_archivos_antiguos,
    limpiar_cache_apps,
    limpiar_cache_yay,
    limpiar_miniaturas,
    limpiar_papelera,
)


class TestFormatoEspacio(TestCase):
    def test_bytes(self):
        self.assertEqual(_formato_espacio(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(_formato_espacio(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(_formato_espacio(5 * 1024 * 1024), "5.0 MB")

    def test_gigabytes(self):
        self.assertEqual(_formato_espacio(2 * 1024 ** 3), "2.00 GB")


class TestLimpiarArchivosAntiguos(TestCase):
    def test_elimina_archivos_viejos(self):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            viejo = ruta / "viejo.txt"
            viejo.write_text("datos")
            hace_10_dias = time.time() - (10 * 86400)
            import os
            os.utime(viejo, (hace_10_dias, hace_10_dias))

            limite = time.time() - (7 * 86400)
            liberado = _limpiar_archivos_antiguos(ruta, limite)

            self.assertGreater(liberado, 0)
            self.assertFalse(viejo.exists())

    def test_no_elimina_archivos_recientes(self):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            reciente = ruta / "reciente.txt"
            reciente.write_text("datos")

            limite = time.time() - (7 * 86400)
            liberado = _limpiar_archivos_antiguos(ruta, limite)

            self.assertEqual(liberado, 0)
            self.assertTrue(reciente.exists())


class TestDryRun(TestCase):
    def test_dry_run_no_borra_archivos(self):
        mod.MODO_SECO = True
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            viejo = ruta / "viejo.txt"
            viejo.write_text("datos")
            hace_10_dias = time.time() - (10 * 86400)
            import os
            os.utime(viejo, (hace_10_dias, hace_10_dias))

            limite = time.time() - (7 * 86400)
            liberado = _limpiar_archivos_antiguos(ruta, limite)

            self.assertGreater(liberado, 0)
            self.assertTrue(viejo.exists())
        mod.MODO_SECO = False

    @patch("limpiador.limpiador.logger")
    def test_dry_run_papelera_no_borra(self, mock_logger):
        mod.MODO_SECO = True
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            (ruta / "basura.txt").write_text("eliminar")

            liberado = limpiar_papelera(ruta)
            self.assertGreater(liberado, 0)
            self.assertTrue((ruta / "basura.txt").exists())
        mod.MODO_SECO = False


class TestWhitelistCache(TestCase):
    @patch("limpiador.limpiador.logger")
    def test_solo_limpia_whitelist(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            seguro = ruta / "chromium"
            seguro.mkdir()
            (seguro / "viejo.dat").write_text("cache")
            hace_10_dias = time.time() - (10 * 86400)
            import os
            os.utime(seguro / "viejo.dat", (hace_10_dias, hace_10_dias))

            intocable = ruta / "mise"
            intocable.mkdir()
            (intocable / "config.dat").write_text("importante")
            os.utime(intocable / "config.dat", (hace_10_dias, hace_10_dias))

            liberado = limpiar_cache_apps(ruta, 7, ["chromium"])

            self.assertGreater(liberado, 0)
            self.assertFalse((seguro / "viejo.dat").exists())
            self.assertTrue((intocable / "config.dat").exists())

    @patch("limpiador.limpiador.logger")
    def test_whitelist_vacia_no_limpia(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            (ruta / "algo").mkdir()
            (ruta / "algo" / "f.txt").write_text("x")

            liberado = limpiar_cache_apps(ruta, 7, [])
            self.assertEqual(liberado, 0)


class TestCacheYay(TestCase):
    @patch("limpiador.limpiador.logger")
    def test_limpia_src_pkg_viejos(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            pkg = ruta / "mi-paquete-aur"
            (pkg / "src").mkdir(parents=True)
            (pkg / "pkg").mkdir()
            (pkg / "PKGBUILD").write_text("pkgname=mi-paquete-aur")
            (pkg / "src" / "main.c").write_text("int main(){}")
            (pkg / "pkg" / "built.pkg").write_text("pkg")

            hace_40_dias = time.time() - (40 * 86400)
            import os
            for f in pkg.rglob("*"):
                if f.is_file():
                    os.utime(f, (hace_40_dias, hace_40_dias))

            liberado = limpiar_cache_yay(ruta, 30)

            self.assertGreater(liberado, 0)
            self.assertFalse((pkg / "src").exists())
            self.assertFalse((pkg / "pkg").exists())
            self.assertTrue((pkg / "PKGBUILD").exists())

    @patch("limpiador.limpiador.logger")
    def test_no_limpia_recientes(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            pkg = ruta / "paquete"
            (pkg / "src").mkdir(parents=True)
            (pkg / "src" / "main.c").write_text("int main(){}")

            liberado = limpiar_cache_yay(ruta, 30)
            self.assertEqual(liberado, 0)


class TestPapelera(TestCase):
    @patch("limpiador.limpiador.logger")
    def test_papelera_inexistente(self, mock_logger):
        mod.MODO_SECO = False
        resultado = limpiar_papelera(Path("/tmp/papelera_inexistente_test"))
        self.assertEqual(resultado, 0)

    @patch("limpiador.limpiador.logger")
    def test_vacia_papelera(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            (ruta / "basura.txt").write_text("eliminar")
            (ruta / "subdir").mkdir()
            (ruta / "subdir" / "otro.txt").write_text("eliminar")

            liberado = limpiar_papelera(ruta)

            self.assertGreater(liberado, 0)
            remaining = list(ruta.iterdir())
            self.assertEqual(len(remaining), 0)


class TestMiniaturas(TestCase):
    @patch("limpiador.limpiador.logger")
    def test_directorio_inexistente(self, mock_logger):
        mod.MODO_SECO = False
        resultado = limpiar_miniaturas(Path("/tmp/minis_inexistente_test"))
        self.assertEqual(resultado, 0)

    @patch("limpiador.limpiador.logger")
    def test_elimina_miniaturas(self, mock_logger):
        mod.MODO_SECO = False
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp)
            (ruta / "thumb1.png").write_bytes(b"\x00" * 1024)
            (ruta / "thumb2.png").write_bytes(b"\x00" * 1024)

            liberado = limpiar_miniaturas(ruta)

            self.assertEqual(liberado, 2048)
            archivos = list(ruta.rglob("*.png"))
            self.assertEqual(len(archivos), 0)


if __name__ == "__main__":
    main()
