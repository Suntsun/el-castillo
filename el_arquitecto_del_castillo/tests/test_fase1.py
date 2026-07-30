"""
Tests de la Fase 1 del rediseno del Arquitecto del Castillo.

Cubre:
    - comun.opencode: wrapper subprocess del CLI OpenCode (smoke tests).
    - arquitecto.registro: carga y validacion de manifiestos.toml.

Convencion: los tests que dependen de servicios externos (OpenCode contra
modelo remoto) se marcan con @unittest.skipUnless para no fallar en entornos
sin red o sin modelo configurado.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Anhadir la raiz de automatizaciones al sys.path para poder importar
# tanto `comun.*` como `arquitecto.*`.
_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
_PAQUETE_ARQUI = _AQUI.parent.parent

for ruta in (_RAIZ_AUTOMATIZACIONES, _PAQUETE_ARQUI):
    if str(ruta) not in sys.path:
        sys.path.insert(0, str(ruta))

from comun import opencode  # noqa: E402
from arquitecto.registro import (  # noqa: E402
    Manifiesto,
    cargar_manifiesto,
    cargar_registro,
    vista_para_cerebro,
)


RUTA_PILOTO = Path.home() / "Escritorio/automatizaciones/cronista_errores/manifiesto.toml"
RUTA_BASE = Path.home() / "Escritorio/automatizaciones"


# -- comun.opencode ------------------------------------------------------------


class TestOpencodeDisponibilidad(unittest.TestCase):
    """Smoke test del binario."""

    def test_opencode_disponible(self) -> None:
        self.assertTrue(
            opencode.disponible(),
            "Se asume que el binario 'opencode' esta instalado y funcional",
        )


@unittest.skipUnless(
    opencode.disponible(),
    "opencode no esta disponible en este entorno",
)
class TestOpencodeSesion(unittest.TestCase):
    """Verifica que se puede crear sesion y enviar/recibir contra OpenCode.

    Depende de red y del modelo configurado en OpenCode; si en algun
    momento el entorno no tiene credenciales o el modelo cae, el test se
    desactiva automaticamente con skip dinamico.
    """

    def test_opencode_envia_y_recibe(self) -> None:
        sid = opencode.nueva_sesion()
        if sid is None:
            self.skipTest("nueva_sesion devolvio None (modelo o red no disponibles)")
        self.assertTrue(sid.startswith("ses_"), f"sessionID inesperado: {sid}")

        respuesta = opencode.enviar(sid, "responde solo OK", timeout_s=60)
        if respuesta is None:
            self.skipTest("enviar devolvio None (probable timeout o error del modelo)")
        self.assertIn("ok", respuesta.lower(), f"respuesta no contiene OK: {respuesta!r}")


# -- arquitecto.registro -------------------------------------------------------


class TestRegistro(unittest.TestCase):

    def test_cargar_manifiesto_piloto(self) -> None:
        m = cargar_manifiesto(RUTA_PILOTO)
        self.assertIsNotNone(m, "el manifiesto piloto deberia cargar")
        assert m is not None  # ayuda al type-checker
        self.assertIsInstance(m, Manifiesto)
        self.assertEqual(m.clave, "cronista_errores")
        self.assertEqual(len(m.operaciones), 5)
        # operacion() debe encontrar una existente y devolver None para otras.
        self.assertIsNotNone(m.operacion("mostrar_24h"))
        self.assertIsNone(m.operacion("inexistente"))

    def test_cargar_registro_descubre_piloto(self) -> None:
        reg = cargar_registro(RUTA_BASE)
        self.assertIn(
            "cronista_errores", reg,
            f"piloto no encontrado; claves={sorted(reg.keys())}",
        )
        self.assertIsInstance(reg["cronista_errores"], Manifiesto)

    def test_manifiesto_invalido_rechazado(self) -> None:
        """Un TOML sin meta.clave debe devolver None (no lanzar)."""
        toml_invalido = b"""
[meta]
nombre_visible = "Sin clave"

[invocacion]
comando_base = "x"
tipo = "wrapper_cli"
usa_subcomandos = false

[[operaciones]]
nombre = "op"
descripcion = "x"
flags = []
argumentos_aceptados = []
requiere_confirmacion = false
peligrosidad = "lectura"
bloquea_terminal = false
salida_esperada = "texto_corto"

[seguridad]
permite_argumentos_libres = false
requiere_red = false
requiere_sudo = false
tiempo_max_segundos = 5

[dependencias]
binarios = []
paquetes_python = []
ficheros_config = []
servicios_systemd = []

[contexto_llm]
cuando_usar = "x"
cuando_no_usar = "x"
ejemplos_peticion = ["a", "b"]
palabras_clave = ["k"]
"""
        with tempfile.TemporaryDirectory() as tmp:
            ruta_dir = Path(tmp) / "fakeauto"
            ruta_dir.mkdir()
            ruta_fichero = ruta_dir / "manifiesto.toml"
            ruta_fichero.write_bytes(toml_invalido)

            m = cargar_manifiesto(ruta_fichero)
            self.assertIsNone(m, "manifiesto sin meta.clave deberia ser rechazado")

    def test_vista_para_cerebro_es_compacta(self) -> None:
        reg = cargar_registro(RUTA_BASE)
        vista = vista_para_cerebro(reg)
        self.assertIsInstance(vista, str)
        # La vista incluye operaciones+args de cada manifiesto, asi que
        # crece con el ecosistema. Cota generosa pero acotada: ~1 KB por
        # automatizacion de media debe sobrar y seguir entrando en contexto.
        self.assertLess(len(vista.encode("utf-8")), 32 * 1024,
                        "vista para cerebro deberia mantenerse acotada")
        self.assertIn("cronista_errores", vista)

    def test_vista_para_cerebro_incluye_operaciones(self) -> None:
        reg = cargar_registro(RUTA_BASE)
        vista = vista_para_cerebro(reg)
        # Las operaciones REALES deben aparecer con su nombre exacto, para
        # que el cerebro no se las invente (regresion: antes solo veia la
        # automatizacion, no las operaciones).
        self.assertIn("mostrar_semana", vista)
        self.assertIn("mostrar_historial", vista)
        self.assertNotIn("mostrar_todo", vista)  # nombre inventado, no existe
        # La operacion que bloquea la terminal se marca como solo-manual.
        self.assertIn("seguir_en_vivo", vista)
        self.assertIn("solo-manual", vista)
        # La operacion de escritura se marca como que pide confirmacion.
        self.assertIn("limpiar_log_global", vista)
        self.assertIn("confirma", vista)


if __name__ == "__main__":
    unittest.main(verbosity=2)
