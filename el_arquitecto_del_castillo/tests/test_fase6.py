"""
Tests de la Fase 6b del rediseno del Arquitecto: decision `delegar_opencode`.

Cubre:
    - validador._validar_delegar_opencode
    - seguridad.evaluar_delegacion / resolver_delegacion
    - ejecutor.delegar_a_opencode (con comun.opencode.delegar mockeado)
    - repl.procesar_respuesta rama delegar_opencode

OpenCode se mockea siempre (no se hace ninguna llamada real). El sandbox
de escritura se redirige a un directorio temporal.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_AQUI = Path(__file__).resolve()
_RAIZ = _AQUI.parent.parent.parent
_PAQUETE = _AQUI.parent.parent
for _r in (_RAIZ, _PAQUETE):
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))

from arquitecto import ejecutor, repl, seguridad, trazas  # noqa: E402
from arquitecto.cerebro import RespuestaCerebro  # noqa: E402
from arquitecto.validador import validar_decision  # noqa: E402


def _resp_delegar(norm: dict, *, valida=True) -> RespuestaCerebro:
    return RespuestaCerebro(
        decision="delegar_opencode", bruto=dict(norm), normalizada=dict(norm),
        valida=valida, reintentos=0, requiere_confirmacion=True, turno_id="d1",
    )


_NORM_LECTURA = {
    "decision": "delegar_opencode",
    "tarea": "analiza la arquitectura del proyecto en profundidad",
    "ambito": "lectura", "razon": "tarea compleja de analisis",
    "requiere_confirmacion": True,
}
_NORM_ESCRITURA = {
    "decision": "delegar_opencode",
    "tarea": "crea un script hola.py que imprima hola",
    "ambito": "escritura", "razon": "generar codigo nuevo",
    "requiere_confirmacion": True,
}


# -- Validador -----------------------------------------------------------------


class TestValidadorDelegar(unittest.TestCase):

    def test_delegar_lectura_valido(self):
        ok, motivo, norm = validar_decision({
            "decision": "delegar_opencode", "tarea": "analiza esto",
            "ambito": "lectura", "razon": "complejo"}, {})
        self.assertTrue(ok, motivo)
        self.assertTrue(norm["requiere_confirmacion"])
        self.assertEqual(norm["ambito"], "lectura")

    def test_delegar_escritura_valido(self):
        ok, motivo, norm = validar_decision({
            "decision": "delegar_opencode", "tarea": "crea un fichero",
            "ambito": "escritura", "razon": "generar"}, {})
        self.assertTrue(ok, motivo)

    def test_ambito_invalido_se_rechaza(self):
        ok, motivo, _ = validar_decision({
            "decision": "delegar_opencode", "tarea": "x",
            "ambito": "borrar_todo", "razon": "y"}, {})
        self.assertFalse(ok)
        self.assertIn("ambito", motivo)

    def test_falta_tarea_se_rechaza(self):
        ok, _, _ = validar_decision({
            "decision": "delegar_opencode", "ambito": "lectura",
            "razon": "y"}, {})
        self.assertFalse(ok)

    def test_campo_extra_se_rechaza(self):
        ok, motivo, _ = validar_decision({
            "decision": "delegar_opencode", "tarea": "x", "ambito": "lectura",
            "razon": "y", "directorio": "/etc"}, {})
        self.assertFalse(ok)
        self.assertIn("extra", motivo)


# -- Seguridad -----------------------------------------------------------------


class TestSeguridadDelegar(unittest.TestCase):

    def test_evaluar_siempre_confirma(self):
        v = seguridad.evaluar_delegacion(_NORM_LECTURA)
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)
        self.assertIn("analiza", v.texto_confirmacion)

    def test_resolver_lectura_y_escritura(self):
        ag_l, dir_l = seguridad.resolver_delegacion("lectura")
        ag_e, dir_e = seguridad.resolver_delegacion("escritura")
        self.assertEqual(ag_l, "arquitecto-lectura")
        self.assertEqual(ag_e, "arquitecto-escritura")
        self.assertEqual(dir_e, seguridad.SANDBOX_ESCRITURA)

    def test_ambito_invalido_bloquea(self):
        v = seguridad.evaluar_delegacion({"ambito": "ninguno", "tarea": "x"})
        self.assertFalse(v.permitido)


# -- Ejecutor ------------------------------------------------------------------


class TestEjecutorDelegar(unittest.TestCase):

    def test_lectura_confirmada_delega(self):
        with patch.object(ejecutor.opencode, "delegar",
                          return_value="analisis hecho") as mock_del:
            res = ejecutor.delegar_a_opencode(
                _NORM_LECTURA, confirmador=lambda _t: True)
        mock_del.assert_called_once()
        self.assertTrue(res.exito)
        self.assertEqual(res.stdout, "analisis hecho")
        self.assertEqual(res.nombre_operacion, "delegar_lectura")

    def test_sin_confirmador_no_delega(self):
        with patch.object(ejecutor.opencode, "delegar") as mock_del:
            res = ejecutor.delegar_a_opencode(_NORM_LECTURA)
        mock_del.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("confirmacion", res.motivo_no_ejecucion)

    def test_rechazada_no_delega(self):
        with patch.object(ejecutor.opencode, "delegar") as mock_del:
            res = ejecutor.delegar_a_opencode(
                _NORM_LECTURA, confirmador=lambda _t: False)
        mock_del.assert_not_called()
        self.assertFalse(res.ejecutado)

    def test_dry_run_no_delega(self):
        with patch.object(ejecutor.opencode, "delegar") as mock_del:
            res = ejecutor.delegar_a_opencode(
                _NORM_LECTURA, confirmador=lambda _t: True, dry_run=True)
        mock_del.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("dry-run", res.motivo_no_ejecucion)

    def test_escritura_crea_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = str(Path(tmp) / "arqui-sandbox")
            with patch.object(seguridad, "SANDBOX_ESCRITURA", sandbox), \
                 patch.object(ejecutor.opencode, "delegar",
                              return_value="fichero creado") as mock_del:
                res = ejecutor.delegar_a_opencode(
                    _NORM_ESCRITURA, confirmador=lambda _t: True)
            self.assertTrue(res.exito)
            self.assertTrue(Path(sandbox).is_dir(), "debe crear el sandbox")
            # Se delego con el agente de escritura y el sandbox correcto.
            _, kwargs = mock_del.call_args
            self.assertEqual(kwargs["agente"], "arquitecto-escritura")
            self.assertEqual(kwargs["directorio"], sandbox)

    def test_opencode_devuelve_none(self):
        with patch.object(ejecutor.opencode, "delegar", return_value=None):
            res = ejecutor.delegar_a_opencode(
                _NORM_LECTURA, confirmador=lambda _t: True)
        self.assertTrue(res.ejecutado)
        self.assertEqual(res.codigo_salida, 1)
        self.assertIsNotNone(res.error)


# -- REPL ----------------------------------------------------------------------


class TestReplDelegar(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self._tmp.name) / "trazas.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_procesar_delegar_lectura(self):
        with patch.object(ejecutor.opencode, "delegar",
                          return_value="hecho"):
            r = repl.procesar_respuesta(
                _resp_delegar(_NORM_LECTURA), {},
                confirmador=lambda _t: True,
                ruta_trazas=self.ruta, peticion_usuario="analiza el repo",
            )
        self.assertEqual(r.decision, "delegar_opencode")
        self.assertTrue(r.ejecuto_algo)
        leidas = trazas.leer_trazas(ruta=self.ruta)
        self.assertEqual(leidas[0]["decision"], "delegar_opencode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
