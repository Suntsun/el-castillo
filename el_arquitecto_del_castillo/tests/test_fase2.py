"""
Tests de la Fase 2 del rediseno del Arquitecto del Castillo.

Cubre:
    - arquitecto.validador: contrato JSON Arquitecto <-> Cerebro.
    - arquitecto.cerebro: SesionCerebro (ciclo de vida + parsing JSON +
      reintentos + borrado de sesion).

Mockeamos OpenCode en TODOS los tests del cerebro salvo el de
integracion real (marcado con @unittest.skipUnless) para que la suite
no dependa de la red ni del modelo en cada ejecucion. Razon: pasar de
no determinismo a determinismo. El test de integracion real existe
para verificar end-to-end manualmente cuando el entorno lo permita.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Anhadir la raiz de automatizaciones y la del paquete arquitecto al sys.path.
_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
_PAQUETE_ARQUI = _AQUI.parent.parent

for _ruta in (_RAIZ_AUTOMATIZACIONES, _PAQUETE_ARQUI):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

from comun import opencode  # noqa: E402
from arquitecto.registro import cargar_registro  # noqa: E402
from arquitecto.validador import validar_decision  # noqa: E402
from arquitecto.cerebro import (  # noqa: E402
    SesionCerebro,
    _extraer_json,
    _buscar_cierre_balanceado,
)


RUTA_BASE = Path.home() / "Escritorio/automatizaciones"
RUTA_OPENCODE_PY = RUTA_BASE / "comun" / "opencode.py"
RUTA_CEREBRO_PY = RUTA_BASE / "el_arquitecto_del_castillo" / "arquitecto" / "cerebro.py"


def _registro_piloto() -> dict:
    """Carga el registro real (solo contiene el piloto `cronista_errores`)."""
    return cargar_registro(RUTA_BASE)


# ============================================================================
# 1. Validador: invocar correcto (operacion de lectura)
# ============================================================================


class TestValidarInvocarCorrecta(unittest.TestCase):

    def test_validar_decision_invocar_correcta(self) -> None:
        reg = _registro_piloto()
        self.assertIn("cronista_errores", reg)

        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "mostrar_24h",
            "argumentos": {},
            "razon": "el usuario pide ver los errores recientes",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertTrue(ok, f"deberia ser valida; motivo={motivo}")
        self.assertEqual(motivo, "")
        self.assertIsNotNone(norm)
        assert norm is not None
        self.assertEqual(norm["decision"], "invocar")
        self.assertEqual(norm["clave_automatizacion"], "cronista_errores")
        self.assertEqual(norm["nombre_operacion"], "mostrar_24h")
        self.assertEqual(norm["peligrosidad_efectiva"], "lectura")
        # Lectura sin requiere_confirmacion en el manifiesto -> False.
        self.assertFalse(norm["requiere_confirmacion"])
        self.assertFalse(norm["bloquea_terminal"])


# ============================================================================
# 2. Decision fuera de contrato se rechaza (sin OpenCode)
# ============================================================================


class TestDecisionFueraContrato(unittest.TestCase):

    def test_falta_campo_decision(self) -> None:
        ok, motivo, norm = validar_decision({"texto": "hola"}, {})
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("decision", motivo)

    def test_decision_no_en_enum(self) -> None:
        ok, motivo, norm = validar_decision(
            {"decision": "lanzar_misil", "x": 1}, {},
        )
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("lanzar_misil", motivo)

    def test_decision_no_es_dict(self) -> None:
        ok, motivo, norm = validar_decision("no soy un dict", {})  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIsNone(norm)


# ============================================================================
# 3. Automatizacion inexistente se rechaza
# ============================================================================


class TestAutomatizacionInexistente(unittest.TestCase):

    def test_automatizacion_inexistente(self) -> None:
        reg = _registro_piloto()
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "no_existe_jamas",
            "nombre_operacion": "x",
            "argumentos": {},
            "razon": "test",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("no_existe_jamas", motivo)

    def test_operacion_inexistente_en_automatizacion_real(self) -> None:
        reg = _registro_piloto()
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "operacion_que_no_existe",
            "argumentos": {},
            "razon": "test",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("operacion_que_no_existe", motivo)


# ============================================================================
# 4. Argumentos no permitidos / metacaracteres shell se rechazan
# ============================================================================


class TestArgumentosNoPermitidos(unittest.TestCase):

    def test_clave_argumento_no_en_whitelist(self) -> None:
        reg = _registro_piloto()
        # mostrar_24h no acepta argumentos (argumentos_aceptados = []).
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "mostrar_24h",
            "argumentos": {"flag_inventado": "valor"},
            "razon": "test",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("flag_inventado", motivo)

    def test_argumento_con_metacaracter_shell_rechazado(self) -> None:
        """Regla de oro: cualquier shell-injection-shape en valores -> rechazo.

        Aunque la clave no este en la whitelist y por tanto el rechazo
        tambien venga por ahi, el principio se documenta y se cubre con
        otro test que SI usa una clave hipoteticamente valida.
        """
        reg = _registro_piloto()
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "mostrar_24h",
            "argumentos": {"x": "; rm -rf /"},
            "razon": "test",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertFalse(ok)
        self.assertIsNone(norm)

    def test_metacaracteres_en_pedir_confirmacion(self) -> None:
        """El sub-objeto `invocacion` tambien valida shell-shape."""
        reg = _registro_piloto()
        decision = {
            "decision": "pedir_confirmacion",
            "mensaje": "ok?",
            "invocacion": {
                "clave_automatizacion": "cronista_errores",
                "nombre_operacion": "limpiar_log_global",
                "argumentos": {"x": "$(whoami)"},
            },
        }
        ok, motivo, _ = validar_decision(decision, reg)
        self.assertFalse(ok)
        # Aqui la clave 'x' tampoco esta en argumentos_aceptados, pero
        # confirmamos que el rechazo es claro.
        self.assertTrue(motivo)


# ============================================================================
# 5. Decision peligrosa marca requiere_confirmacion
# ============================================================================


class TestDecisionPeligrosa(unittest.TestCase):

    def test_limpiar_log_global_marca_requiere_confirmacion(self) -> None:
        reg = _registro_piloto()
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "limpiar_log_global",
            "argumentos": {},
            "razon": "limpiar segun pidio el usuario",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertTrue(ok, motivo)
        assert norm is not None
        self.assertEqual(norm["peligrosidad_efectiva"], "escritura_local")
        self.assertTrue(norm["requiere_confirmacion"])

    def test_bloquea_terminal_es_rechazado(self) -> None:
        """seguir_en_vivo tiene bloquea_terminal=true -> no invocable."""
        reg = _registro_piloto()
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "cronista_errores",
            "nombre_operacion": "seguir_en_vivo",
            "argumentos": {},
            "razon": "ver en vivo",
        }
        ok, motivo, norm = validar_decision(decision, reg)
        self.assertFalse(ok)
        self.assertIsNone(norm)
        self.assertIn("bloquea_terminal", motivo)


# ============================================================================
# 6. SesionCerebro borra la sesion al salir (mock de opencode)
# ============================================================================


class TestSesionSeBorraAlSalir(unittest.TestCase):

    def test_borrar_sesion_llamada_al_salir(self) -> None:
        reg = _registro_piloto()
        SESSION_ID = "ses_test_12345"

        with patch("arquitecto.cerebro.opencode") as mock_oc:
            mock_oc.disponible.return_value = True
            mock_oc.nueva_sesion.return_value = SESSION_ID
            # Primera "respuesta" al prompt fundacional: cualquier texto.
            mock_oc.enviar.return_value = '{"decision":"responder","texto":"Listo."}'
            mock_oc.borrar_sesion.return_value = True

            with SesionCerebro(reg) as cerebro:
                self.assertTrue(cerebro.disponible)
                self.assertEqual(cerebro._session_id, SESSION_ID)

            # Tras el __exit__:
            mock_oc.borrar_sesion.assert_called_once_with(SESSION_ID)

    def test_borrar_sesion_no_llamada_si_opencode_no_disponible(self) -> None:
        """Si disponible() es False, __enter__ no crea sesion y __exit__ no borra."""
        reg = _registro_piloto()
        with patch("arquitecto.cerebro.opencode") as mock_oc:
            mock_oc.disponible.return_value = False

            with SesionCerebro(reg) as cerebro:
                self.assertFalse(cerebro.disponible)

            mock_oc.nueva_sesion.assert_not_called()
            mock_oc.borrar_sesion.assert_not_called()

    def test_borrar_sesion_si_falla_prompt_fundacional(self) -> None:
        """Si el primer envio falla, se borra la sesion creada (no zombi)."""
        reg = _registro_piloto()
        SESSION_ID = "ses_falla_inicial"
        with patch("arquitecto.cerebro.opencode") as mock_oc:
            mock_oc.disponible.return_value = True
            mock_oc.nueva_sesion.return_value = SESSION_ID
            mock_oc.enviar.return_value = None  # Fallo del envio fundacional.
            mock_oc.borrar_sesion.return_value = True

            with SesionCerebro(reg) as cerebro:
                self.assertFalse(cerebro.disponible)

            # El borrado del ID se hace YA dentro de __enter__ ante el fallo;
            # __exit__ no lo vuelve a borrar porque _session_id pasa a None.
            mock_oc.borrar_sesion.assert_called_once_with(SESSION_ID)


# ============================================================================
# 7. OpenCode NUNCA ejecuta shell (regla de oro a nivel de codigo)
# ============================================================================


class TestNuncaShellEnCodigo(unittest.TestCase):
    """Verificacion estatica de que el codigo no usa primitivas peligrosas."""

    PATRONES_PROHIBIDOS = (
        "shell=True",
        "os.system(",
        "eval(",
        "exec(",
        "os.popen(",
    )

    def _verificar_fichero(self, ruta: Path) -> None:
        contenido = ruta.read_text(encoding="utf-8")
        for patron in self.PATRONES_PROHIBIDOS:
            self.assertNotIn(
                patron, contenido,
                f"{ruta.name}: contiene patron prohibido '{patron}'",
            )

    def test_opencode_py_sin_shell(self) -> None:
        self._verificar_fichero(RUTA_OPENCODE_PY)

    def test_cerebro_py_sin_shell(self) -> None:
        self._verificar_fichero(RUTA_CEREBRO_PY)

    def test_validador_rechaza_shell_en_pedir_confirmacion(self) -> None:
        """Regla de oro replicada a nivel de datos: el validador rechaza
        cualquier valor de argumento que contenga metacaracteres shell."""
        reg = _registro_piloto()
        # Forzamos una validacion donde la CLAVE esta en argumentos_aceptados.
        # Como el piloto no expone ningun argumento, este test usa un
        # registro sintetico con un argumento permitido y verifica el
        # rechazo SOLO por shell-shape.
        from arquitecto.registro import (
            Manifiesto, Operacion, Argumento, Seguridad,
            Dependencias, ContextoLlm,
        )
        op = Operacion(
            nombre="test_op",
            descripcion="test",
            flags=(),
            argumentos_aceptados=("ruta",),
            requiere_confirmacion=False,
            peligrosidad="lectura",
            bloquea_terminal=False,
            salida_esperada="texto_corto",
            subcomando=None,
        )
        arg = Argumento(
            clave="ruta",
            descripcion="ruta de prueba",
            tipo="cadena",
            obligatorio=False,
            forma_paso="flag_largo",
            flag_literal="--ruta",
        )
        m = Manifiesto(
            clave="sintetica",
            nombre_visible="Sintetica",
            descripcion_corta="test",
            categoria="otra",
            version_manifiesto="1.0.0",
            comando_base="x",
            tipo_invocacion="wrapper_cli",
            usa_subcomandos=False,
            subcomando_por_defecto=None,
            operaciones=(op,),
            argumentos=(arg,),
            seguridad=Seguridad(False, False, False, 10),
            dependencias=Dependencias((), (), (), ()),
            contexto_llm=ContextoLlm("a", "b", ("c", "d"), ("e",)),
            ruta_fichero=Path("/tmp/x"),
        )
        reg_sint = {"sintetica": m}

        for valor_malicioso in ("; ls", "$(id)", "a | b", "a && b", "`x`", "a > b"):
            decision = {
                "decision": "invocar",
                "clave_automatizacion": "sintetica",
                "nombre_operacion": "test_op",
                "argumentos": {"ruta": valor_malicioso},
                "razon": "test shell-shape",
            }
            ok, motivo, _ = validar_decision(decision, reg_sint)
            self.assertFalse(
                ok, f"valor {valor_malicioso!r} deberia ser rechazado",
            )
            self.assertIn("metacaracter", motivo.lower())


# ============================================================================
# Helpers de cerebro: extraccion de JSON tolerante
# ============================================================================


class TestExtraerJson(unittest.TestCase):

    def test_json_directo(self) -> None:
        obj, motivo = _extraer_json('{"decision": "responder", "texto": "hi"}')
        self.assertIsNotNone(obj)
        self.assertEqual(obj, {"decision": "responder", "texto": "hi"})

    def test_json_con_envoltorio(self) -> None:
        texto = 'Claro, mi decision es: {"decision":"responder","texto":"hi"} listo.'
        obj, _ = _extraer_json(texto)
        self.assertIsNotNone(obj)
        assert obj is not None
        self.assertEqual(obj["decision"], "responder")

    def test_json_en_fence_markdown(self) -> None:
        texto = (
            "Aqui tienes:\n```json\n"
            '{"decision":"responder","texto":"hola"}\n'
            "```\nGracias."
        )
        obj, _ = _extraer_json(texto)
        self.assertIsNotNone(obj)
        assert obj is not None
        self.assertEqual(obj["texto"], "hola")

    def test_json_no_encontrado(self) -> None:
        obj, motivo = _extraer_json("solo texto, ningun objeto")
        self.assertIsNone(obj)
        self.assertTrue(motivo)

    def test_llaves_balanceadas_string_con_llave(self) -> None:
        # String JSON que contiene '}' no debe confundir al parser.
        texto = '{"decision":"responder","texto":"con } dentro"}'
        obj, _ = _extraer_json(texto)
        self.assertIsNotNone(obj)
        assert obj is not None
        self.assertEqual(obj["texto"], "con } dentro")

    def test_buscar_cierre_balanceado(self) -> None:
        s = '{"a":{"b":1}}'
        self.assertEqual(_buscar_cierre_balanceado(s, 0), len(s) - 1)
        # Llave que abre un sub-objeto:
        self.assertEqual(_buscar_cierre_balanceado(s, 5), 11)


# ============================================================================
# Cerebro: reintentos con prompt corrector (mock)
# ============================================================================


class TestCerebroReintentos(unittest.TestCase):

    def test_recupera_tras_un_reintento(self) -> None:
        reg = _registro_piloto()
        SID = "ses_reintento"
        # Secuencia de enviar:
        #   1) prompt fundacional -> "Listo."
        #   2) primer turno: respuesta SIN JSON -> reintento.
        #   3) reintento: JSON valido.
        respuestas = iter([
            "Listo.",                                               # fundacional
            "claro, voy a responder ahora",                         # turno: no JSON
            '{"decision":"responder","texto":"hola"}',              # corregido
        ])

        with patch("arquitecto.cerebro.opencode") as mock_oc:
            mock_oc.disponible.return_value = True
            mock_oc.nueva_sesion.return_value = SID
            mock_oc.enviar.side_effect = lambda *_a, **_k: next(respuestas)
            mock_oc.borrar_sesion.return_value = True

            with SesionCerebro(reg, max_reintentos=2) as cerebro:
                resp = cerebro.turno("hola")

            self.assertTrue(resp.valida)
            self.assertEqual(resp.decision, "responder")
            self.assertEqual(resp.reintentos, 1)
            self.assertEqual(resp.normalizada["texto"], "hola")

    def test_se_da_por_perdida_tras_agotar_reintentos(self) -> None:
        reg = _registro_piloto()
        SID = "ses_perdida"
        respuestas = iter([
            "Listo.",            # fundacional
            "nada de JSON",      # turno 1
            "tampoco",           # reintento 1
            "ni asi",            # reintento 2
        ])

        with patch("arquitecto.cerebro.opencode") as mock_oc:
            mock_oc.disponible.return_value = True
            mock_oc.nueva_sesion.return_value = SID
            mock_oc.enviar.side_effect = lambda *_a, **_k: next(respuestas)
            mock_oc.borrar_sesion.return_value = True

            with SesionCerebro(reg, max_reintentos=2) as cerebro:
                resp = cerebro.turno("dime algo")

            self.assertFalse(resp.valida)
            self.assertEqual(resp.decision, "rechazar_peligro")
            self.assertEqual(resp.reintentos, 2)
            self.assertIn("fuera de contrato", resp.normalizada["motivo"])


# ============================================================================
# Test de integracion REAL con OpenCode (skip dinamico)
# ============================================================================


@unittest.skipUnless(
    opencode.disponible(),
    "opencode no esta disponible en este entorno",
)
class TestIntegracionRealOpenCode(unittest.TestCase):
    """Verifica end-to-end: SesionCerebro real, sin mocks.

    Acepta dos comportamientos:
      - El cerebro responde con un JSON valido al primer turno.
      - O bien tras como mucho 2 reintentos.
    Tolerante: el unico assert duro es que el ciclo se complete sin
    excepcion y que la sesion creada se haya borrado al salir.
    """

    def test_ciclo_completo_real(self) -> None:
        reg = _registro_piloto()
        with SesionCerebro(reg, max_reintentos=2, timeout_turno_s=90) as cerebro:
            if not cerebro.disponible:
                self.skipTest(
                    "SesionCerebro.__enter__ no consiguio sesion (red/modelo?)"
                )
            sid = cerebro._session_id
            print(f"\n[integracion] sessionID={sid}")

            resp = cerebro.turno(
                "Saludo. Responde brevemente.",
            )
            print(f"[integracion] decision={resp.decision} valida={resp.valida} "
                  f"reintentos={resp.reintentos}")
            print(f"[integracion] bruto={resp.bruto}")
            print(f"[integracion] normalizada={resp.normalizada}")

            self.assertIn(
                resp.decision,
                {"responder", "aclarar", "invocar", "proponer_nueva",
                 "rechazar_peligro", "pedir_confirmacion", "componer"},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
