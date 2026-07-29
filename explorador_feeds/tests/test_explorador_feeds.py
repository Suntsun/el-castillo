#!/usr/bin/env python3
"""Tests para explorador_feeds."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explorador_feeds import (
    parsear_rss,
    parsear_atom,
    parsear_feed,
    cargar_fuentes,
    añadir_fuente,
    borrar_fuente,
    guardar_fuentes,
    cargar_cache,
    guardar_cache,
    resumir_articulo,
    _parsear_fecha,
    _hace_tiempo,
)


# -- XML de prueba -----------------------------------------------------

RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Articulo de prueba</title>
      <link>https://ejemplo.com/articulo1</link>
      <description>Descripcion del articulo de prueba.</description>
      <pubDate>Mon, 26 May 2025 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Segundo articulo</title>
      <link>https://ejemplo.com/articulo2</link>
      <description>Otra descripcion.</description>
      <pubDate>Sun, 25 May 2025 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test Feed</title>
  <entry>
    <title>Entrada Atom 1</title>
    <link href="https://atom.ejemplo.com/entrada1" rel="alternate"/>
    <summary>Resumen de la entrada Atom.</summary>
    <published>2025-05-26T12:00:00Z</published>
  </entry>
  <entry>
    <title>Entrada Atom 2</title>
    <link href="https://atom.ejemplo.com/entrada2"/>
    <content>Contenido completo de la segunda entrada.</content>
    <updated>2025-05-25T09:00:00Z</updated>
  </entry>
</feed>
"""

RSS_VACIO = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed vacio</title>
  </channel>
</rss>
"""


# -- Tests de parseo RSS -----------------------------------------------

class TestParsearRSS(TestCase):
    def test_parsea_items(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertEqual(len(arts), 2)

    def test_titulo(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertEqual(arts[0]["titulo"], "Articulo de prueba")

    def test_url(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertEqual(arts[0]["url"], "https://ejemplo.com/articulo1")

    def test_fuente(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertEqual(arts[0]["fuente"], "TestFeed")

    def test_contenido_desde_descripcion(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertEqual(arts[0]["contenido"], "Descripcion del articulo de prueba.")

    def test_fecha_parseada(self):
        arts = parsear_rss(RSS_XML, "TestFeed")
        self.assertIn("2025", arts[0]["fecha"])

    def test_feed_vacio(self):
        arts = parsear_rss(RSS_VACIO, "Vacio")
        self.assertEqual(arts, [])

    def test_xml_invalido(self):
        arts = parsear_rss("esto no es xml", "Roto")
        self.assertEqual(arts, [])


# -- Tests de parseo Atom ----------------------------------------------

class TestParsearAtom(TestCase):
    def test_parsea_entries(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(len(arts), 2)

    def test_titulo_atom(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(arts[0]["titulo"], "Entrada Atom 1")

    def test_url_atom_rel_alternate(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(arts[0]["url"], "https://atom.ejemplo.com/entrada1")

    def test_url_atom_sin_rel(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(arts[1]["url"], "https://atom.ejemplo.com/entrada2")

    def test_contenido_desde_summary(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(arts[0]["contenido"], "Resumen de la entrada Atom.")

    def test_contenido_desde_content(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertEqual(arts[1]["contenido"], "Contenido completo de la segunda entrada.")

    def test_fecha_published(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        self.assertIn("2025", arts[0]["fecha"])

    def test_fecha_updated_fallback(self):
        arts = parsear_atom(ATOM_XML, "AtomTest")
        # Segunda entrada usa updated porque no tiene published
        self.assertIn("2025", arts[1]["fecha"])


# -- Tests de deteccion de formato ------------------------------------

class TestParsearFeed(TestCase):
    def test_detecta_rss(self):
        arts = parsear_feed(RSS_XML, "RSS")
        self.assertEqual(len(arts), 2)

    def test_detecta_atom(self):
        arts = parsear_feed(ATOM_XML, "Atom")
        self.assertEqual(len(arts), 2)

    def test_xml_invalido(self):
        arts = parsear_feed("no xml", "Nada")
        self.assertEqual(arts, [])


# -- Tests de fuentes --------------------------------------------------

class TestFuentes(TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".toml", mode="w", delete=False, encoding="utf-8"
        )
        self._tmp.write('[[fuente]]\nnombre = "Test"\nurl = "https://test.com/rss"\ncategoria = "test"\n')
        self._tmp.close()
        self._ruta_original = None

    def tearDown(self):
        Path(self._tmp.name).unlink(missing_ok=True)
        if self._ruta_original is not None:
            self._restaurar_ruta()

    def _patchear_ruta(self):
        """Reemplaza RUTA_FUENTES con el fichero temporal."""
        import explorador_feeds as mod
        self._ruta_original = mod.RUTA_FUENTES
        mod.RUTA_FUENTES = Path(self._tmp.name)

    def _restaurar_ruta(self):
        import explorador_feeds as mod
        mod.RUTA_FUENTES = self._ruta_original

    def test_cargar_fuentes(self):
        self._patchear_ruta()
        fuentes = cargar_fuentes()
        self.assertEqual(len(fuentes), 1)
        self.assertEqual(fuentes[0]["nombre"], "Test")

    def test_añadir_fuente(self):
        self._patchear_ruta()
        nombre = añadir_fuente("https://nueva.com/feed", nombre="Nueva")
        self.assertEqual(nombre, "Nueva")
        fuentes = cargar_fuentes()
        self.assertEqual(len(fuentes), 2)

    def test_añadir_fuente_duplicada(self):
        self._patchear_ruta()
        nombre = añadir_fuente("https://test.com/rss")
        self.assertEqual(nombre, "Test")
        fuentes = cargar_fuentes()
        self.assertEqual(len(fuentes), 1)

    def test_borrar_fuente(self):
        self._patchear_ruta()
        ok = borrar_fuente("Test")
        self.assertTrue(ok)
        fuentes = cargar_fuentes()
        self.assertEqual(len(fuentes), 0)

    def test_borrar_fuente_inexistente(self):
        self._patchear_ruta()
        ok = borrar_fuente("NoExiste")
        self.assertFalse(ok)

    def test_listar_fuentes(self):
        self._patchear_ruta()
        fuentes = cargar_fuentes()
        self.assertEqual(fuentes[0]["url"], "https://test.com/rss")
        self.assertEqual(fuentes[0]["categoria"], "test")


# -- Tests de cache ----------------------------------------------------

class TestCache(TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8"
        )
        self._tmp.write("[]")
        self._tmp.close()
        self._config = {"cache": {"ruta": self._tmp.name}}

    def tearDown(self):
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_cache_vacia(self):
        arts = cargar_cache(self._config)
        self.assertEqual(arts, [])

    def test_guardar_y_cargar(self):
        datos = [{"titulo": "Test", "url": "https://t.com", "fuente": "F",
                  "fecha": "", "contenido": "algo", "resumen": ""}]
        guardar_cache(datos, self._config)
        cargados = cargar_cache(self._config)
        self.assertEqual(len(cargados), 1)
        self.assertEqual(cargados[0]["titulo"], "Test")

    def test_cache_fichero_no_existe(self):
        config = {"cache": {"ruta": "/tmp/no_existe_explorador_test.json"}}
        arts = cargar_cache(config)
        self.assertEqual(arts, [])

    def test_cache_json_corrupto(self):
        Path(self._tmp.name).write_text("{corrupto", encoding="utf-8")
        arts = cargar_cache(self._config)
        self.assertEqual(arts, [])


# -- Tests de resumen LLM ---------------------------------------------

class TestResumenLLM(TestCase):
    @patch("explorador_feeds.llm_disponible", return_value=True)
    @patch("explorador_feeds.consultar_llm",
           return_value="El articulo habla de Linux. Es muy interesante.")
    def test_resume_con_llm(self, mock_llm, mock_disp):
        resumen = resumir_articulo("Contenido largo sobre Linux y cosas...")
        self.assertIsNotNone(resumen)
        self.assertIn("Linux", resumen)
        mock_llm.assert_called_once()

    @patch("explorador_feeds.llm_disponible", return_value=False)
    def test_sin_llm_devuelve_none(self, mock_disp):
        resumen = resumir_articulo("Contenido cualquiera")
        self.assertIsNone(resumen)


# -- Tests de formato de fecha -----------------------------------------

class TestParsearFecha(TestCase):
    def test_rfc2822(self):
        fecha = _parsear_fecha("Mon, 26 May 2025 10:00:00 +0000")
        self.assertIn("2025", fecha)

    def test_iso8601(self):
        fecha = _parsear_fecha("2025-05-26T12:00:00Z")
        self.assertIn("2025", fecha)

    def test_vacia(self):
        fecha = _parsear_fecha("")
        self.assertEqual(fecha, "")

    def test_none(self):
        fecha = _parsear_fecha(None)
        self.assertEqual(fecha, "")


class TestHaceTiempo(TestCase):
    def test_sin_fecha(self):
        self.assertEqual(_hace_tiempo(""), "sin fecha")

    def test_fecha_valida(self):
        # No podemos testear tiempos relativos exactos, pero no debe fallar
        resultado = _hace_tiempo("2025-05-26T10:00:00+00:00")
        self.assertIsInstance(resultado, str)
        self.assertTrue(len(resultado) > 0)


class TestValidacionURL(TestCase):
    """N-005: añadir_fuente valida URL; borrar_fuente rechaza nombre vacío."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmppath = Path(self._tmpdir.name) / "fuentes.toml"
        self._patcher = patch("explorador_feeds.RUTA_FUENTES", self._tmppath)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_url_sin_esquema_rechazada(self):
        with self.assertRaises(SystemExit) as ctx:
            añadir_fuente("no-es-una-url")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_url_esquema_ftp_rechazada(self):
        with self.assertRaises(SystemExit) as ctx:
            añadir_fuente("ftp://ejemplo.com/feed.xml")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_url_https_aceptada(self):
        nombre = añadir_fuente("https://ejemplo.com/feed.xml", nombre="Ejemplo")
        self.assertEqual(nombre, "Ejemplo")

    def test_url_http_aceptada(self):
        nombre = añadir_fuente("http://blog.ejemplo.com/rss", nombre="Blog")
        self.assertEqual(nombre, "Blog")

    def test_borrar_nombre_vacio_rechazado(self):
        with self.assertRaises(SystemExit) as ctx:
            borrar_fuente("")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_borrar_nombre_espacios_rechazado(self):
        with self.assertRaises(SystemExit) as ctx:
            borrar_fuente("   ")
        self.assertNotEqual(ctx.exception.code, 0)

    def test_borrar_exacto_no_borra_parcial(self):
        """Coincidencia exacta: 'Test' no borra 'Testing'."""
        # Añadir fuente "Testing"
        añadir_fuente("https://testing.com/rss", nombre="Testing")
        # Borrar por "Test" NO debe borrar "Testing"
        resultado = borrar_fuente("Test")
        self.assertFalse(resultado)
        fuentes = cargar_fuentes()
        self.assertEqual(len(fuentes), 1)
        self.assertEqual(fuentes[0]["nombre"], "Testing")


class TestMainBorrarVacio(TestCase):
    """N-005 (residual): --borrar "" debe dar error, no caer en la lista por defecto."""

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmppath = Path(self._tmpdir.name) / "fuentes.toml"
        self._patcher = patch("explorador_feeds.RUTA_FUENTES", self._tmppath)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def _run_main(self, args_list):
        """Ejecuta main() con los args dados; captura SystemExit."""
        import explorador_feeds as mod
        with patch("sys.argv", ["feeds"] + args_list):
            with patch.object(mod, "cargar_config", return_value={}):
                try:
                    mod.main()
                    return None  # Sin SystemExit
                except SystemExit as e:
                    return e.code

    def test_borrar_cadena_vacia_da_error(self):
        """--borrar "" debe invocar borrar_fuente("") que hace sys.exit(1)."""
        codigo = self._run_main(["--borrar", ""])
        self.assertIsNotNone(codigo)
        self.assertNotEqual(codigo, 0)

    def test_borrar_nombre_inexistente_no_error(self):
        """--borrar "NoExiste" no debe hacer sys.exit (solo imprime "no encontrado")."""
        import io
        import explorador_feeds as mod
        with patch("sys.argv", ["feeds", "--borrar", "NoExiste"]):
            with patch.object(mod, "cargar_config", return_value={}):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    try:
                        mod.main()
                        codigo = 0
                    except SystemExit as e:
                        codigo = e.code
        self.assertEqual(codigo, 0)


class TestMostrarArticuloCompletoExitCode(TestCase):
    """ERR-S01: mostrar_articulo_completo con índice fuera de rango → sys.exit(1)."""

    def _articulos_demo(self):
        return [
            {"titulo": "Art 1", "fuente": "Demo", "fecha": "", "url": "", "resumen": ""},
            {"titulo": "Art 2", "fuente": "Demo", "fecha": "", "url": "", "resumen": ""},
        ]

    def test_indice_cero_sale_1(self):
        """feeds --leer 0 (1-based, 0 es inválido) → sys.exit(1)."""
        from explorador_feeds import mostrar_articulo_completo
        with self.assertRaises(SystemExit) as ctx:
            mostrar_articulo_completo(self._articulos_demo(), 0)
        self.assertEqual(ctx.exception.code, 1)

    def test_indice_negativo_sale_1(self):
        """feeds --leer -1 (negativo) → sys.exit(1)."""
        from explorador_feeds import mostrar_articulo_completo
        with self.assertRaises(SystemExit) as ctx:
            mostrar_articulo_completo(self._articulos_demo(), -1)
        self.assertEqual(ctx.exception.code, 1)

    def test_indice_mayor_que_total_sale_1(self):
        """feeds --leer 99 con solo 2 artículos → sys.exit(1)."""
        from explorador_feeds import mostrar_articulo_completo
        with self.assertRaises(SystemExit) as ctx:
            mostrar_articulo_completo(self._articulos_demo(), 99)
        self.assertEqual(ctx.exception.code, 1)

    def test_indice_valido_no_sale(self):
        """feeds --leer 1 (válido) → no lanza SystemExit."""
        from explorador_feeds import mostrar_articulo_completo
        import io
        with patch("sys.stdout", new_callable=io.StringIO):
            try:
                mostrar_articulo_completo(self._articulos_demo(), 1)
            except SystemExit:
                self.fail("mostrar_articulo_completo levantó SystemExit con índice válido")


if __name__ == "__main__":
    main()
