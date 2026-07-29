#!/usr/bin/env python3
"""Tests para purificador_datos."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from purificador_datos import (
    _formato_tamano,
    _parsear_tamano_minimo,
    _ruta_corta,
    recolectar_archivos,
    agrupar_por_tamano,
    agrupar_por_hash_parcial,
    agrupar_por_hash_completo,
    detectar_duplicados,
    calcular_espacio_recuperable,
    generar_informe,
    guardar_informe,
    enviar_notificacion,
)


class TestFormatoTamano(unittest.TestCase):
    """Tests para el formateo de tamanos legibles."""

    def test_bytes(self):
        self.assertEqual(_formato_tamano(0), "0 B")
        self.assertEqual(_formato_tamano(145), "145 B")
        self.assertEqual(_formato_tamano(1023), "1023 B")

    def test_kilobytes(self):
        self.assertEqual(_formato_tamano(1024), "1.0 KB")
        self.assertEqual(_formato_tamano(3277), "3.2 KB")

    def test_megabytes(self):
        self.assertEqual(_formato_tamano(1024 ** 2), "1.0 MB")
        self.assertEqual(_formato_tamano(int(1.5 * 1024 ** 2)), "1.5 MB")

    def test_gigabytes(self):
        self.assertEqual(_formato_tamano(1024 ** 3), "1.0 GB")
        self.assertEqual(_formato_tamano(int(4.2 * 1024 ** 3)), "4.2 GB")


class TestParsearTamanoMinimo(unittest.TestCase):
    """Tests para el parseo de tamano minimo desde texto."""

    def test_megabytes(self):
        self.assertEqual(_parsear_tamano_minimo("1M"), 1024 ** 2)

    def test_kilobytes(self):
        self.assertEqual(_parsear_tamano_minimo("500K"), 500 * 1024)

    def test_gigabytes(self):
        self.assertEqual(_parsear_tamano_minimo("2G"), 2 * 1024 ** 3)

    def test_bytes_puro(self):
        self.assertEqual(_parsear_tamano_minimo("4096"), 4096)

    def test_minusculas(self):
        self.assertEqual(_parsear_tamano_minimo("1m"), 1024 ** 2)

    def test_vacio(self):
        self.assertEqual(_parsear_tamano_minimo(""), 0)

    def test_invalido(self):
        self.assertEqual(_parsear_tamano_minimo("abc"), 0)


class TestRutaCorta(unittest.TestCase):
    """Tests para la conversion de rutas a formato corto."""

    def test_ruta_en_home(self):
        ruta = Path.home() / "Descargas" / "archivo.txt"
        self.assertEqual(_ruta_corta(ruta), "~/Descargas/archivo.txt")

    def test_ruta_fuera_de_home(self):
        ruta = Path("/tmp/archivo.txt")
        self.assertEqual(_ruta_corta(ruta), "/tmp/archivo.txt")


class TestRecolectarArchivos(unittest.TestCase):
    """Tests para la recoleccion de archivos candidatos."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        return ruta

    def test_recolecta_archivos_normales(self):
        self._crear_archivo("a.txt", b"hola mundo contenido")
        self._crear_archivo("b.txt", b"otro contenido aqui")

        archivos = recolectar_archivos([self.ruta])
        self.assertEqual(len(archivos), 2)

    def test_excluye_archivos_pequenos(self):
        self._crear_archivo("pequeno.txt", b"x")
        self._crear_archivo("grande.txt", b"x" * 2000)

        archivos = recolectar_archivos([self.ruta], min_bytes=1024)
        self.assertEqual(len(archivos), 1)

    def test_excluye_extensiones(self):
        self._crear_archivo("normal.txt", b"contenido normal aqui")
        self._crear_archivo("temporal.tmp", b"contenido temporal")

        archivos = recolectar_archivos([self.ruta], ignorar_ext={".tmp"})
        self.assertEqual(len(archivos), 1)
        self.assertTrue(archivos[0].name.endswith(".txt"))

    def test_excluye_carpetas(self):
        self._crear_archivo("normal.txt", b"contenido normal ok")
        self._crear_archivo(".git/config", b"contenido git config")
        self._crear_archivo("node_modules/paquete.js", b"contenido js")

        archivos = recolectar_archivos(
            [self.ruta], ignorar_dirs={".git", "node_modules"}
        )
        self.assertEqual(len(archivos), 1)

    def test_excluye_archivos_vacios(self):
        self._crear_archivo("vacio.txt", b"")
        self._crear_archivo("lleno.txt", b"tiene contenido")

        archivos = recolectar_archivos([self.ruta])
        self.assertEqual(len(archivos), 1)

    def test_respeta_max_archivos(self):
        for i in range(20):
            self._crear_archivo(f"archivo_{i}.txt", b"contenido" * 10)

        archivos = recolectar_archivos([self.ruta], max_archivos=5)
        self.assertEqual(len(archivos), 5)

    def test_carpeta_inexistente(self):
        archivos = recolectar_archivos([Path("/no/existe/esta/ruta")])
        self.assertEqual(len(archivos), 0)

    def test_no_sigue_symlinks(self):
        self._crear_archivo("real.txt", b"contenido real archivo")
        enlace = self.ruta / "enlace.txt"
        os.symlink(self.ruta / "real.txt", enlace)

        archivos = recolectar_archivos([self.ruta])
        nombres = [a.name for a in archivos]
        self.assertIn("real.txt", nombres)
        self.assertNotIn("enlace.txt", nombres)


class TestAgruparPorTamano(unittest.TestCase):
    """Tests para la agrupacion de archivos por tamano."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    def test_agrupa_mismo_tamano(self):
        a = self._crear_archivo("a.txt", b"1234567890")
        b = self._crear_archivo("b.txt", b"abcdefghij")

        grupos = agrupar_por_tamano([a, b])
        self.assertEqual(len(grupos), 1)
        tamano = list(grupos.keys())[0]
        self.assertEqual(tamano, 10)
        self.assertEqual(len(grupos[tamano]), 2)

    def test_no_agrupa_distintos(self):
        a = self._crear_archivo("a.txt", b"corto")
        b = self._crear_archivo("b.txt", b"mucho mas largo que el otro")

        grupos = agrupar_por_tamano([a, b])
        self.assertEqual(len(grupos), 0)

    def test_multiples_grupos(self):
        a1 = self._crear_archivo("a1.txt", b"12345")
        a2 = self._crear_archivo("a2.txt", b"abcde")
        b1 = self._crear_archivo("b1.txt", b"1234567890")
        b2 = self._crear_archivo("b2.txt", b"abcdefghij")

        grupos = agrupar_por_tamano([a1, a2, b1, b2])
        self.assertEqual(len(grupos), 2)


class TestHashParcial(unittest.TestCase):
    """Tests para la agrupacion por hash parcial."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    def test_mismo_contenido_mismo_hash(self):
        a = self._crear_archivo("a.txt", b"contenido identico")
        b = self._crear_archivo("b.txt", b"contenido identico")

        grupos = agrupar_por_hash_parcial([a, b])
        self.assertEqual(len(grupos), 1)
        self.assertEqual(len(list(grupos.values())[0]), 2)

    def test_distinto_contenido_distinto_hash(self):
        a = self._crear_archivo("a.txt", b"contenido A")
        b = self._crear_archivo("b.txt", b"contenido B")

        grupos = agrupar_por_hash_parcial([a, b])
        self.assertEqual(len(grupos), 0)


class TestHashCompleto(unittest.TestCase):
    """Tests para la verificacion por hash completo."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    def test_duplicados_confirmados(self):
        contenido = b"este es el contenido duplicado exacto"
        a = self._crear_archivo("a.txt", contenido)
        b = self._crear_archivo("b.txt", contenido)

        grupos = agrupar_por_hash_completo([a, b])
        self.assertEqual(len(grupos), 1)

    def test_archivos_grandes_distintos(self):
        # Archivos de mismo tamano pero distinto contenido
        a = self._crear_archivo("a.bin", b"\x00" * 10000)
        b = self._crear_archivo("b.bin", b"\x01" * 10000)

        grupos = agrupar_por_hash_completo([a, b])
        self.assertEqual(len(grupos), 0)


class TestDetectarDuplicados(unittest.TestCase):
    """Tests para el pipeline completo de deteccion."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        return ruta

    def test_detecta_duplicados_reales(self):
        contenido = b"archivo duplicado con contenido suficiente"
        self._crear_archivo("a.txt", contenido)
        self._crear_archivo("sub/b.txt", contenido)
        self._crear_archivo("unico.txt", b"contenido unico diferente ok")

        duplicados = detectar_duplicados([self.ruta], min_bytes=0)
        self.assertEqual(len(duplicados), 1)
        self.assertEqual(len(duplicados[0]), 2)

    def test_sin_duplicados(self):
        self._crear_archivo("a.txt", b"contenido unico A valido")
        self._crear_archivo("b.txt", b"contenido unico B distinto")
        self._crear_archivo("c.txt", b"contenido unico C otro mas")

        duplicados = detectar_duplicados([self.ruta], min_bytes=0)
        self.assertEqual(len(duplicados), 0)

    def test_multiples_grupos(self):
        self._crear_archivo("a1.txt", b"grupo A duplicado contenido largo")
        self._crear_archivo("a2.txt", b"grupo A duplicado contenido largo")
        self._crear_archivo("b1.txt", b"grupo B duplicado contenido distinto")
        self._crear_archivo("b2.txt", b"grupo B duplicado contenido distinto")

        duplicados = detectar_duplicados([self.ruta], min_bytes=0)
        self.assertEqual(len(duplicados), 2)

    def test_tres_copias(self):
        contenido = b"triple duplicado con contenido suficiente"
        self._crear_archivo("copia1.txt", contenido)
        self._crear_archivo("copia2.txt", contenido)
        self._crear_archivo("sub/copia3.txt", contenido)

        duplicados = detectar_duplicados([self.ruta], min_bytes=0)
        self.assertEqual(len(duplicados), 1)
        self.assertEqual(len(duplicados[0]), 3)

    def test_respeta_min_bytes(self):
        contenido = b"corto"
        self._crear_archivo("a.txt", contenido)
        self._crear_archivo("b.txt", contenido)

        duplicados = detectar_duplicados([self.ruta], min_bytes=1024)
        self.assertEqual(len(duplicados), 0)

    def test_excluye_carpetas_ignoradas(self):
        contenido = b"contenido para probar exclusion de carpetas"
        self._crear_archivo("real.txt", contenido)
        self._crear_archivo(".git/objects/abc", contenido)

        duplicados = detectar_duplicados(
            [self.ruta], min_bytes=0, ignorar_dirs={".git"}
        )
        self.assertEqual(len(duplicados), 0)

    def test_multiples_carpetas(self):
        dir_a = Path(tempfile.mkdtemp())
        dir_b = Path(tempfile.mkdtemp())
        contenido = b"duplicado entre carpetas con contenido largo"
        (dir_a / "archivo.txt").write_bytes(contenido)
        (dir_b / "copia.txt").write_bytes(contenido)

        duplicados = detectar_duplicados([dir_a, dir_b], min_bytes=0)
        self.assertEqual(len(duplicados), 1)


class TestCalcularEspacioRecuperable(unittest.TestCase):
    """Tests para el calculo de espacio recuperable."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    def test_espacio_dos_copias(self):
        contenido = b"x" * 1000
        a = self._crear_archivo("a.txt", contenido)
        b = self._crear_archivo("b.txt", contenido)

        espacio = calcular_espacio_recuperable([[a, b]])
        # Se conserva 1 copia, se recupera el tamano de la otra
        self.assertEqual(espacio, 1000)

    def test_espacio_tres_copias(self):
        contenido = b"x" * 500
        a = self._crear_archivo("a.txt", contenido)
        b = self._crear_archivo("b.txt", contenido)
        c = self._crear_archivo("c.txt", contenido)

        espacio = calcular_espacio_recuperable([[a, b, c]])
        self.assertEqual(espacio, 1000)

    def test_sin_duplicados(self):
        espacio = calcular_espacio_recuperable([])
        self.assertEqual(espacio, 0)


class TestGenerarInforme(unittest.TestCase):
    """Tests para la generacion de informes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    def test_informe_con_duplicados(self):
        contenido = b"duplicado en informe para test"
        a = self._crear_archivo("a.txt", contenido)
        b = self._crear_archivo("b.txt", contenido)

        texto = generar_informe([[a, b]], [self.ruta])

        self.assertIn("INFORME DE DUPLICADOS", texto)
        self.assertIn("RESUMEN", texto)
        self.assertIn("DETALLE", texto)
        self.assertIn("2 copias", texto)

    def test_informe_sin_duplicados(self):
        texto = generar_informe([], [self.ruta])

        self.assertIn("INFORME DE DUPLICADOS", texto)
        self.assertIn("0", texto)
        self.assertNotIn("DETALLE", texto)


class TestGuardarInforme(unittest.TestCase):
    """Tests para guardar informes en disco."""

    def test_guarda_correctamente(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("purificador_datos.Path.home") as mock_home:
                mock_home.return_value = Path(tmpdir)
                texto = "INFORME DE PRUEBA"
                ruta = guardar_informe(texto)

                self.assertTrue(ruta.exists())
                self.assertTrue(ruta.name.startswith("duplicados_"))
                self.assertEqual(ruta.read_text(), texto)


class TestEnviarNotificacion(unittest.TestCase):
    """Tests para el envio de notificaciones."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ruta = Path(self.tmpdir)

    def _crear_archivo(self, nombre: str, contenido: bytes) -> Path:
        ruta = self.ruta / nombre
        ruta.write_bytes(contenido)
        return ruta

    @patch("purificador_datos.notificar")
    def test_notifica_con_duplicados(self, mock_notificar):
        contenido = b"duplicado para test notificacion"
        a = self._crear_archivo("a.txt", contenido)
        b = self._crear_archivo("b.txt", contenido)
        config = {"notificacion": {"duracion": 8000, "severidad": "info"}}

        enviar_notificacion([[a, b]], config)

        mock_notificar.assert_called_once()
        args = mock_notificar.call_args
        self.assertIn("1 duplicados", args[0][1])
        self.assertIn("recuperables", args[0][1])
        self.assertEqual(args[0][2], "info")

    @patch("purificador_datos.notificar")
    def test_notifica_sin_duplicados(self, mock_notificar):
        config = {}

        enviar_notificacion([], config)

        mock_notificar.assert_called_once()
        args = mock_notificar.call_args
        self.assertIn("Sin duplicados", args[0][1])
        self.assertEqual(args[0][2], "exito")


# ── R6-002: dupes <fichero> → exit≠0 (con reserva: config-carpeta inexistente ok) ─

class TestR6002DupesValidacion(unittest.TestCase):
    """R6-002: rutas explicitas invalidas → exit≠0; config invalida → warning, sin error."""

    def _run_main_with_args(self, argv):
        """Ejecuta main() con los args dados y devuelve el codigo de salida."""
        import io
        from purificador_datos import main
        with patch("sys.argv", ["dupes"] + argv):
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", new_callable=io.StringIO):
                    main()
        return ctx.exception.code

    def test_fichero_en_lugar_de_dir_sale_nonzero(self):
        """dupes <fichero> → exit≠0 (un fichero no es un directorio)."""
        with tempfile.NamedTemporaryFile(suffix=".txt") as f:
            code = self._run_main_with_args([f.name])
        self.assertNotEqual(code, 0)

    def test_ruta_inexistente_explicita_sale_nonzero(self):
        """dupes /ruta/que/no/existe → exit≠0."""
        code = self._run_main_with_args(["/tmp/directorio_que_no_existe_xyz_r6002"])
        self.assertNotEqual(code, 0)

    def test_dir_real_con_cero_dupes_sale_0(self):
        """dupes <dir_real_con_0_dupes> → exit 0 (camino feliz sin regresion)."""
        with tempfile.TemporaryDirectory() as d:
            # Crear 2 archivos distintos
            Path(d, "a.txt").write_bytes(b"contenido unico A para test r6002")
            Path(d, "b.txt").write_bytes(b"contenido unico B para test r6002")
            with patch("purificador_datos.cargar_config", return_value={}):
                with patch("purificador_datos.notificar"):
                    with patch("purificador_datos.guardar_informe", return_value=Path("/tmp/test.txt")):
                        # main no debe llamar sys.exit con error
                        import io
                        from purificador_datos import main
                        with patch("sys.argv", ["dupes", d]):
                            with patch("sys.stdout", new_callable=io.StringIO):
                                # Si no lanza SystemExit, el test pasa (exit 0 implícito)
                                # Si lanza con code 0, también pasa
                                try:
                                    main()
                                    exit_code = 0
                                except SystemExit as e:
                                    exit_code = e.code or 0
            self.assertEqual(exit_code, 0)

    def test_config_carpeta_inexistente_no_falla(self):
        """Carpeta de config inexistente → warning, sin error (reserva R6-002)."""
        from purificador_datos import detectar_duplicados
        # Carpeta que no existe: detectar_duplicados debe devolver lista vacía,
        # no lanzar excepcion. La validacion de is_dir en detectar_duplicados
        # ya hace warning+continue.
        carpeta_falsa = Path("/tmp/carpeta_inexistente_r6002_xyz")
        duplicados = detectar_duplicados([carpeta_falsa], min_bytes=0)
        self.assertEqual(duplicados, [])


if __name__ == "__main__":
    unittest.main()
