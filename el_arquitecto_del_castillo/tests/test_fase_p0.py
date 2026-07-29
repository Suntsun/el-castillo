#!/usr/bin/env python3
"""
Tests de la Fase P0 — Eliminacion de la doble arquitectura peligrosa.

Objetivo verificado aqui:
    1. El entrypoint (`arqui` -> monolito.main) arranca SOLO el camino
       gobernado (`repl_cerebro`).
    2. Si OpenCode no esta disponible, el sistema degrada a FALLO SEGURO:
       NO cae al bucle legacy `repl()`, NO ejecuta Qwen, NO ejecuta shell.
    3. No queda ningun `shell=True` (ni primitiva equivalente) alcanzable
       desde el monolito.
    4. La generacion de comandos shell por LLM esta neutralizada.

NO se testea el bucle interactivo en si (E/S por terminal); se testea el
enrutado de `main()` con dobles de prueba.
"""

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

RUTA_MONOLITO = _RAIZ_ARQUI / "el_arquitecto_del_castillo.py"
RUTA_ARQUI_BIN = Path.home() / ".local" / "bin" / "arqui"


# ============================================================================
# 1. Verificacion estatica: el monolito no tiene shell=True ni primitivas
#    de ejecucion arbitraria.
# ============================================================================


class TestSinShellEjecutableEnMonolito(unittest.TestCase):
    """El monolito ya no contiene primitivas de shell libre."""

    PATRONES_PROHIBIDOS = (
        "shell=True",
        "os.system(",
        "os.popen(",
    )

    def test_monolito_sin_primitivas_shell(self) -> None:
        contenido = RUTA_MONOLITO.read_text(encoding="utf-8")
        for patron in self.PATRONES_PROHIBIDOS:
            self.assertNotIn(
                patron, contenido,
                f"el_arquitecto_del_castillo.py contiene patron prohibido "
                f"'{patron}' (P0 exige eliminarlo)",
            )


# ============================================================================
# 2. Neutralizacion de las primitivas legacy.
# ============================================================================


class TestPrimitivasLegacyNeutralizadas(unittest.TestCase):
    """`ejecutar_comando` no lanza procesos; `_generar_comando_fs` no genera
    shell."""

    def test_ejecutar_comando_no_lanza_subproceso(self) -> None:
        with patch("el_arquitecto_del_castillo.subprocess.run") as mock_run:
            with redirect_stdout(io.StringIO()):
                mono.ejecutar_comando("echo deberia-estar-prohibido")
            mock_run.assert_not_called()

    def test_generar_comando_fs_devuelve_none(self) -> None:
        # El generador de shell por LLM (Qwen) esta ELIMINADO: siempre None.
        resultado = mono._generar_comando_fs("borra todo", "listar", {})
        self.assertIsNone(resultado)


# ============================================================================
# 3. main(): arranca SOLO el camino gobernado.
# ============================================================================


class TestEntrypointCaminoGobernado(unittest.TestCase):
    """`main()` usa repl_cerebro y nunca el bucle legacy `repl()`."""

    def test_opencode_disponible_usa_camino_gobernado(self) -> None:
        mock_gobernado = MagicMock(return_value=True)
        with patch("arquitecto.repl.repl_cerebro", mock_gobernado), \
             patch("el_arquitecto_del_castillo.repl") as mock_legacy:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                mono.main()
        mock_gobernado.assert_called_once()
        mock_legacy.assert_not_called()

    def test_opencode_no_disponible_no_cae_a_legacy(self) -> None:
        mock_gobernado = MagicMock(return_value=False)
        err = io.StringIO()
        with patch("arquitecto.repl.repl_cerebro", mock_gobernado), \
             patch("el_arquitecto_del_castillo.repl") as mock_legacy, \
             patch("el_arquitecto_del_castillo._registrar_fallo_seguro"):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                mono.main()
        mock_gobernado.assert_called_once()
        mock_legacy.assert_not_called()
        # Muestra el fallo seguro canonico, no un fallback a Qwen.
        self.assertIn("no se ejecutará ninguna acción", err.getvalue())

    def test_importerror_del_paquete_no_cae_a_legacy(self) -> None:
        err = io.StringIO()
        # Forzar que `from arquitecto.repl import repl_cerebro` falle.
        with patch.dict(sys.modules, {"arquitecto.repl": None}), \
             patch("el_arquitecto_del_castillo.repl") as mock_legacy, \
             patch("el_arquitecto_del_castillo._registrar_fallo_seguro"):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                mono.main()
        mock_legacy.assert_not_called()
        self.assertIn("no se ejecutará ninguna acción", err.getvalue())

    def test_main_no_contiene_llamada_al_bucle_legacy(self) -> None:
        import inspect
        fuente = inspect.getsource(mono.main)
        # No debe haber una llamada de fallback al bucle legacy `repl()`.
        self.assertNotIn("repl()", fuente,
                         "main() no debe invocar el bucle legacy repl()")
        self.assertIn("repl_cerebro", fuente)


# ============================================================================
# 4. El wrapper `arqui` apunta al monolito (que ya solo usa el camino
#    gobernado). Test informativo: se omite si el wrapper no existe.
# ============================================================================


class TestWrapperArqui(unittest.TestCase):
    def test_arqui_lanza_el_monolito(self) -> None:
        if not RUTA_ARQUI_BIN.is_file():
            self.skipTest("wrapper ~/.local/bin/arqui no presente en este entorno")
        contenido = RUTA_ARQUI_BIN.read_text(encoding="utf-8")
        self.assertIn("el_arquitecto_del_castillo.py", contenido)


if __name__ == "__main__":
    unittest.main()
