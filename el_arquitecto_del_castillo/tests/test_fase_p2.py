#!/usr/bin/env python3
"""
Tests de la Fase P2 — Limpieza/consolidación de deuda técnica.

Verifican que la limpieza no introdujo rutas peligrosas y que el código
quedó alineado con la arquitectura actual ("Arquitecto soberano con cerebro
OpenCode"):

    - no quedan rutas legacy alcanzables desde `arqui`;
    - no hay `shell=True` (ni primitivas equivalentes) en la cadena;
    - los restos Qwen fueron eliminados;
    - OpenCode no disponible ⇒ fallo seguro (sin legacy);
    - el cerebro usa el agente restringido `arquitecto-cerebro`;
    - la delegación sigue confirmada y confinada;
    - la traza marca `delegar_opencode` como fuera de manifiestos.
"""

import inspect
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

_RAIZ_ARQUI = Path(__file__).resolve().parent.parent
_RAIZ_AUTOS = _RAIZ_ARQUI.parent
for _p in (_RAIZ_ARQUI, _RAIZ_AUTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import el_arquitecto_del_castillo as mono  # noqa: E402
from comun import opencode  # noqa: E402
from arquitecto import seguridad, ejecutor, trazas  # noqa: E402

RUTA_MONOLITO = _RAIZ_ARQUI / "el_arquitecto_del_castillo.py"
DIR_ARQUI = _RAIZ_ARQUI / "arquitecto"


# ============================================================================
# 1. Restos Qwen eliminados.
# ============================================================================


class TestQwenEliminado(unittest.TestCase):

    def test_funciones_qwen_no_existen(self) -> None:
        self.assertFalse(hasattr(mono, "match_por_llm_con_contexto"))
        self.assertFalse(hasattr(mono, "_responder_libre"))

    def test_sin_imports_ni_llamadas_qwen(self) -> None:
        src = RUTA_MONOLITO.read_text(encoding="utf-8")
        self.assertNotIn("consultar_llm", src)
        self.assertNotIn("llm_disponible", src)
        self.assertNotIn("qwen2.5", src)


# ============================================================================
# 2. Sin shell=True en toda la cadena alcanzable desde arqui.
# ============================================================================


class TestSinShellEnLaCadena(unittest.TestCase):
    """Verificacion por AST: detecta USO real de shell=True / os.system /
    os.popen, no meras menciones en comentarios o docstrings (p. ej. el
    docstring de validador.py que explica que NUNCA usa shell=True)."""

    def _ficheros(self):
        yield RUTA_MONOLITO
        for f in sorted(DIR_ARQUI.glob("*.py")):
            yield f

    @staticmethod
    def _usos_peligrosos(ruta: Path) -> list[str]:
        import ast
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        hallazgos: list[str] = []
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            for kw in nodo.keywords:
                if (kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True):
                    hallazgos.append(f"shell=True@L{nodo.lineno}")
            fn = nodo.func
            if (isinstance(fn, ast.Attribute) and fn.attr in ("system", "popen")
                    and isinstance(fn.value, ast.Name) and fn.value.id == "os"):
                hallazgos.append(f"os.{fn.attr}@L{nodo.lineno}")
        return hallazgos

    def test_sin_primitivas_shell(self) -> None:
        for ruta in self._ficheros():
            usos = self._usos_peligrosos(ruta)
            self.assertEqual(
                usos, [],
                f"{ruta.name} usa primitivas de shell prohibidas: {usos}",
            )


# ============================================================================
# 3. No quedan rutas legacy alcanzables desde main().
# ============================================================================


class TestLegacyInalcanzable(unittest.TestCase):

    def test_main_solo_camino_gobernado(self) -> None:
        fuente = inspect.getsource(mono.main)
        self.assertIn("repl_cerebro", fuente)
        self.assertIn("_fallo_seguro", fuente)
        # No invoca el bucle legacy ni el matching legacy.
        self.assertNotIn("repl()", fuente)
        self.assertNotIn("match_por_keyword", fuente)
        self.assertNotIn("ejecutar_comando", fuente)

    def test_repl_legacy_es_stub_inerte(self) -> None:
        with patch("el_arquitecto_del_castillo.subprocess.run") as mrun:
            with redirect_stdout(io.StringIO()):
                resultado = mono.repl()
        self.assertIsNone(resultado)
        mrun.assert_not_called()

    def test_ejecutar_comando_no_lanza(self) -> None:
        with patch("el_arquitecto_del_castillo.subprocess.run") as mrun:
            with redirect_stdout(io.StringIO()):
                mono.ejecutar_comando("echo prohibido")
        mrun.assert_not_called()

    def test_generar_comando_fs_inerte(self) -> None:
        self.assertIsNone(mono._generar_comando_fs("lo que sea", "listar", {}))


# ============================================================================
# 4. OpenCode no disponible ⇒ fallo seguro (sin legacy).
# ============================================================================


class TestFalloSeguro(unittest.TestCase):

    def test_no_disponible_no_cae_a_legacy(self) -> None:
        err = io.StringIO()
        with patch("arquitecto.repl.repl_cerebro", MagicMock(return_value=False)), \
             patch("el_arquitecto_del_castillo.repl") as mock_legacy, \
             patch("el_arquitecto_del_castillo._registrar_fallo_seguro"):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                mono.main()
        mock_legacy.assert_not_called()
        self.assertIn("no se ejecutará ninguna acción", err.getvalue())


# ============================================================================
# 5. Cerebro restringido + delegación confinada (no regresión P1).
# ============================================================================


class TestSeguridadNoRegresion(unittest.TestCase):

    def test_cerebro_usa_agente_restringido(self) -> None:
        fake = MagicMock(
            returncode=0,
            stdout='{"type":"text","sessionID":"s","part":{"type":"text","text":"x"}}\n',
            stderr="",
        )
        with patch("comun.opencode.subprocess.run", return_value=fake) as mrun:
            opencode.enviar("s", "hola")
        comando = mrun.call_args.args[0]
        self.assertIn("--agent", comando)
        self.assertIn("arquitecto-cerebro", comando)

    def test_delegacion_escritura_fuera_bloqueada(self) -> None:
        v = seguridad.evaluar_delegacion({
            "decision": "delegar_opencode", "ambito": "escritura",
            "tarea": "escribe en /etc/hosts", "razon": "x",
        })
        self.assertFalse(v.permitido)

    def test_delegacion_confirmacion_negativa_no_ejecuta(self) -> None:
        with patch("arquitecto.ejecutor.opencode.delegar") as mock_deleg:
            res = ejecutor.delegar_a_opencode(
                {"decision": "delegar_opencode", "ambito": "escritura",
                 "tarea": "crea un README", "razon": "x"},
                confirmador=lambda _t: False,
            )
        mock_deleg.assert_not_called()
        self.assertFalse(res.ejecutado)


# ============================================================================
# 6. Trazabilidad mejorada de delegar_opencode.
# ============================================================================


class TestTrazabilidadDelegacion(unittest.TestCase):

    def test_traza_marca_fuera_de_manifiestos(self) -> None:
        t = trazas.construir_traza(
            peticion_usuario="haz un refactor",
            decision="delegar_opencode",
            valida=True,
        )
        self.assertTrue(t["fuera_de_manifiestos"])

    def test_traza_invocar_no_marca_fuera_de_manifiestos(self) -> None:
        t = trazas.construir_traza(
            peticion_usuario="muestra errores",
            decision="invocar",
            valida=True,
        )
        self.assertFalse(t["fuera_de_manifiestos"])

    def test_traza_captura_avisos(self) -> None:
        res = ejecutor.ResultadoEjecucion(
            clave_automatizacion="opencode",
            nombre_operacion="delegar_escritura",
            comando=("opencode", "run"),
            ejecutado=True,
            codigo_salida=0,
            avisos=("CAPACIDAD EXCEPCIONAL fuera de manifiestos",),
        )
        t = trazas.construir_traza(
            peticion_usuario="x", decision="delegar_opencode", valida=True,
            resultados=[res],
        )
        self.assertIn("avisos", t["ejecuciones"][0])
        self.assertTrue(any("EXCEPCIONAL" in a
                            for a in t["ejecuciones"][0]["avisos"]))


if __name__ == "__main__":
    unittest.main()
