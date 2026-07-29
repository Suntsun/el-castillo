"""
Tests de la Fase 4 del rediseno del Arquitecto del Castillo.

Cubre el nucleo de enrutado del REPL nuevo (`arquitecto.repl`):
`procesar_respuesta` para las 7 decisiones, el flujo de confirmacion de
`pedir_confirmacion`, el manejo de respuestas invalidas y el registro de
trazas. El subprocess se mockea; las trazas van a un fichero temporal.

No se testea el bucle interactivo `repl_cerebro` (E/S por terminal),
igual que el monolito legacy tampoco testea su `repl()`.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
_PAQUETE_ARQUI = _AQUI.parent.parent
for _ruta in (_RAIZ_AUTOMATIZACIONES, _PAQUETE_ARQUI):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

from arquitecto import ejecutor, repl, trazas  # noqa: E402
from arquitecto.cerebro import RespuestaCerebro  # noqa: E402
from arquitecto.registro import (  # noqa: E402
    ContextoLlm,
    Dependencias,
    Manifiesto,
    Operacion,
    Seguridad,
)


# -- Fabricas ------------------------------------------------------------------


def _manifiesto_eco() -> Manifiesto:
    op_lectura = Operacion(
        nombre="saludar", descripcion="saluda", flags=("hola",),
        argumentos_aceptados=(), requiere_confirmacion=False,
        peligrosidad="lectura", bloquea_terminal=False,
        salida_esperada="texto_corto",
    )
    op_escritura = Operacion(
        nombre="borrar", descripcion="borra algo", flags=(),
        argumentos_aceptados=(), requiere_confirmacion=True,
        peligrosidad="escritura_local", bloquea_terminal=False,
        salida_esperada="texto_corto",
    )
    return Manifiesto(
        clave="eco_test", nombre_visible="Eco", descripcion_corta="prueba",
        categoria="otra", version_manifiesto="1.0.0", comando_base="echo",
        tipo_invocacion="comando_sistema", usa_subcomandos=False,
        subcomando_por_defecto=None,
        operaciones=(op_lectura, op_escritura), argumentos=(),
        seguridad=Seguridad(
            permite_argumentos_libres=False, requiere_red=False,
            requiere_sudo=False, tiempo_max_segundos=10, paths_protegidos=(),
        ),
        dependencias=Dependencias((), (), (), ()),
        contexto_llm=ContextoLlm("c", "n", ("a", "b"), ("x",)),
        ruta_fichero=Path("/tmp/eco.toml"),
    )


def _registro() -> dict:
    return {"eco_test": _manifiesto_eco()}


def _resp(decision: str, norm: dict, *, valida: bool = True,
          turno_id: str = "t1") -> RespuestaCerebro:
    return RespuestaCerebro(
        decision=decision, bruto=dict(norm), normalizada=dict(norm),
        valida=valida, motivo_invalidez=None if valida else "fallo",
        reintentos=0,
        requiere_confirmacion=bool(norm.get("requiere_confirmacion", False)),
        turno_id=turno_id,
    )


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


_INV_LECTURA = {
    "decision": "invocar", "clave_automatizacion": "eco_test",
    "nombre_operacion": "saludar", "argumentos": {},
    "peligrosidad_efectiva": "lectura", "requiere_confirmacion": False,
    "bloquea_terminal": False, "razon": "saludar",
}


# -- Tests ---------------------------------------------------------------------


class TestProcesarRespuesta(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self._tmp.name) / "trazas.jsonl"
        self.registro = _registro()

    def tearDown(self):
        self._tmp.cleanup()

    def _procesar(self, respuesta, **kw):
        kw.setdefault("ruta_trazas", self.ruta)
        kw.setdefault("peticion_usuario", "x")
        return repl.procesar_respuesta(respuesta, self.registro, **kw)

    # -- decisiones de solo texto ---------------------------------------------

    def test_responder(self):
        r = self._procesar(_resp("responder", {
            "decision": "responder", "texto": "Hola humano"}))
        self.assertEqual(r.decision, "responder")
        self.assertIn("Hola humano", " ".join(r.mensajes))
        self.assertFalse(r.ejecuto_algo)

    def test_aclarar_con_opciones(self):
        r = self._procesar(_resp("aclarar", {
            "decision": "aclarar", "pregunta": "¿Cual?",
            "opciones": ["uno", "dos"]}))
        texto = " ".join(r.mensajes)
        self.assertIn("¿Cual?", texto)
        self.assertIn("uno", texto)
        self.assertIn("dos", texto)

    def test_proponer_nueva(self):
        r = self._procesar(_resp("proponer_nueva", {
            "decision": "proponer_nueva", "nombre_sugerido": "guardian_x",
            "descripcion": "hace algo", "justificacion": "porque si"}))
        self.assertIn("guardian_x", " ".join(r.mensajes))

    def test_rechazar_peligro(self):
        r = self._procesar(_resp("rechazar_peligro", {
            "decision": "rechazar_peligro", "motivo": "eso es peligroso",
            "sugerencia_segura": "haz esto otro"}))
        texto = " ".join(r.mensajes)
        self.assertIn("peligroso", texto)
        self.assertIn("haz esto otro", texto)

    # -- invocar ---------------------------------------------------------------

    def test_invocar_lectura_ejecuta(self):
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "hola\n", "")) as mock_run:
            r = self._procesar(_resp("invocar", _INV_LECTURA))
        mock_run.assert_called_once()
        self.assertTrue(r.ejecuto_algo)
        self.assertEqual(len(r.resultados), 1)
        self.assertTrue(r.resultados[0].exito)

    def test_invocar_automatizacion_inexistente(self):
        norm = {**_INV_LECTURA, "clave_automatizacion": "no_existe"}
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            r = self._procesar(_resp("invocar", norm))
        mock_run.assert_not_called()
        self.assertFalse(r.ejecuto_algo)
        self.assertTrue(r.resultados[0].bloqueado)

    # -- componer --------------------------------------------------------------

    def test_componer_dos_pasos(self):
        paso = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
            "parar_si_falla": True,
        }
        norm = {"decision": "componer", "razon": "dos cosas",
                "pasos": [dict(paso), dict(paso)]}
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "ok", "")) as mock_run:
            r = self._procesar(_resp("componer", norm))
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(len(r.resultados), 2)
        self.assertTrue(r.ejecuto_algo)

    # -- pedir_confirmacion ----------------------------------------------------

    def _norm_pedir(self):
        return {
            "decision": "pedir_confirmacion",
            "mensaje": "Voy a borrar el log, ¿ok?",
            "invocacion": {
                "clave_automatizacion": "eco_test",
                "nombre_operacion": "borrar", "argumentos": {},
                "peligrosidad_efectiva": "escritura_local",
                "requiere_confirmacion": True, "bloquea_terminal": False,
            },
            "requiere_confirmacion": True,
        }

    def test_pedir_confirmacion_aceptada_ejecuta(self):
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "borrado", "")) as mock_run:
            r = self._procesar(
                _resp("pedir_confirmacion", self._norm_pedir()),
                confirmador=lambda _texto: True,
            )
        mock_run.assert_called_once()
        self.assertTrue(r.ejecuto_algo)

    def test_pedir_confirmacion_rechazada_no_ejecuta(self):
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            r = self._procesar(
                _resp("pedir_confirmacion", self._norm_pedir()),
                confirmador=lambda _texto: False,
            )
        mock_run.assert_not_called()
        self.assertFalse(r.ejecuto_algo)
        self.assertIn("cancelada", " ".join(r.mensajes).lower())

    # -- respuesta invalida ----------------------------------------------------

    def test_respuesta_invalida_se_muestra_y_se_traza(self):
        norm = {"decision": "rechazar_peligro",
                "motivo": "no entendi tu peticion",
                "requiere_confirmacion": False}
        r = self._procesar(_resp("rechazar_peligro", norm, valida=False))
        self.assertIn("no entendi", " ".join(r.mensajes))
        leidas = trazas.leer_trazas(ruta=self.ruta)
        self.assertEqual(len(leidas), 1)
        self.assertFalse(leidas[0]["valida"])

    # -- trazas ----------------------------------------------------------------

    def test_cada_turno_se_traza(self):
        self._procesar(_resp("responder", {
            "decision": "responder", "texto": "uno"}))
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "ok", "")):
            self._procesar(_resp("invocar", _INV_LECTURA))
        leidas = trazas.leer_trazas(ruta=self.ruta)
        self.assertEqual(len(leidas), 2)
        self.assertEqual(leidas[0]["decision"], "responder")
        self.assertEqual(leidas[1]["decision"], "invocar")
        self.assertEqual(len(leidas[1]["ejecuciones"]), 1)


class TestConfirmadorTerminal(unittest.TestCase):
    """Prueba la rama interactiva de confirmador_terminal.

    Se mockea sys.stdin.isatty para devolver True, simulando un TTY real.
    Sin ese mock el confirmador cancela antes de llegar a input() cuando
    unittest no dispone de terminal (comportamiento de produccion correcto,
    pero que impide ejercitar la logica interactiva en tests).
    """

    def test_si_confirma(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="s"):
            self.assertTrue(repl.confirmador_terminal("¿ok?"))

    def test_si_variantes_confirman(self):
        for variante in ("si", "y", "yes", "S", "SI", "Y", "YES"):
            with self.subTest(variante=variante), \
                 patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value=variante):
                self.assertTrue(repl.confirmador_terminal("¿ok?"))

    def test_no_confirma_por_defecto(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value=""):
            self.assertFalse(repl.confirmador_terminal("¿ok?"))

    def test_respuesta_arbitraria_es_no(self):
        for valor in ("no", "n", "nope", "salir", "x"):
            with self.subTest(valor=valor), \
                 patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value=valor):
                self.assertFalse(repl.confirmador_terminal("¿ok?"))

    def test_eof_es_no(self):
        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=EOFError):
            self.assertFalse(repl.confirmador_terminal("¿ok?"))

    def test_sin_tty_cancela_sin_leer(self):
        """Sin TTY el confirmador debe cancelar sin llamar a input()."""
        with patch("sys.stdin.isatty", return_value=False), \
             patch("builtins.input") as mock_input:
            resultado = repl.confirmador_terminal("¿ok?")
        self.assertFalse(resultado)
        mock_input.assert_not_called()


class TestConstruirContextoTurno(unittest.TestCase):
    """R3-001: _construir_contexto_turno genera snapshot factual del último turno.

    El invariante principal es que el render visible al usuario sigue siendo
    determinista (_render_resultado). Esta clase verifica solo el snapshot
    que se reinyecta al cerebro para que no alucine.
    """

    def _res(self, **kwargs):
        """Construye un ResultadoEjecucion mínimo con SimpleNamespace."""
        from types import SimpleNamespace
        defaults = dict(
            clave_automatizacion="eco_test",
            nombre_operacion="saludar",
            ejecutado=True,
            bloqueado=False,
            motivo_no_ejecucion=None,
            error=None,
            timeout=False,
            codigo_salida=0,
            duracion_s=0.5,
            avisos=[],
            stdout="",
            stderr="",
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_sin_resultados_devuelve_vacio(self):
        """Turno de texto puro (sin ejecuciones) → contexto vacío."""
        resultado = repl.ResultadoTurno(
            decision="responder",
            mensajes=("hola",),
            resultados=(),
            ejecuto_algo=False,
        )
        ctx = repl._construir_contexto_turno(resultado)
        self.assertEqual(ctx, "")

    def test_turno_bloqueado_refleja_bloqueo(self):
        """Acción bloqueada → contexto contiene 'BLOQUEADO'."""
        res_bloqueado = self._res(
            ejecutado=False,
            bloqueado=True,
            motivo_no_ejecucion="requiere confirmacion humana",
        )
        resultado = repl.ResultadoTurno(
            decision="invocar",
            mensajes=("bloqueado",),
            resultados=(res_bloqueado,),
            ejecuto_algo=False,
        )
        ctx = repl._construir_contexto_turno(resultado)
        self.assertIn("BLOQUEADO", ctx)
        self.assertIn("eco_test.saludar", ctx)

    def test_turno_exitoso_refleja_exito(self):
        """Acción exitosa → contexto contiene 'EXITO'."""
        res_ok = self._res(ejecutado=True, codigo_salida=0, duracion_s=1.2)
        resultado = repl.ResultadoTurno(
            decision="invocar",
            mensajes=("ok",),
            resultados=(res_ok,),
            ejecuto_algo=True,
        )
        ctx = repl._construir_contexto_turno(resultado)
        self.assertIn("EXITO", ctx)
        self.assertIn("eco_test.saludar", ctx)

    def test_turno_fallido_refleja_fallo(self):
        """Acción con exit≠0 → contexto contiene 'FALLO'."""
        res_fallo = self._res(ejecutado=True, codigo_salida=1, duracion_s=0.3)
        resultado = repl.ResultadoTurno(
            decision="invocar",
            mensajes=("fallo",),
            resultados=(res_fallo,),
            ejecuto_algo=True,
        )
        ctx = repl._construir_contexto_turno(resultado)
        self.assertIn("FALLO", ctx)

    def test_render_sigue_siendo_determinista(self):
        """El render visible al usuario (_render_resultado) no cambia con el nuevo contexto."""
        res_ok = self._res(ejecutado=True, codigo_salida=0, duracion_s=1.0)
        lineas = repl._render_resultado(res_ok)
        # El render debe contener el checkmark de éxito
        texto_render = " ".join(lineas)
        self.assertIn("eco_test.saludar", texto_render)
        # Debe haber una línea con el símbolo de éxito (✓)
        self.assertTrue(any("✓" in l for l in lineas))


if __name__ == "__main__":
    unittest.main(verbosity=2)
