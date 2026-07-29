#!/usr/bin/env python3
"""
Tests de la Fase P1.1 — Restriccion tecnica de la sesion principal del cerebro.

Verifica que:
    1. El comando OpenCode de la sesion del cerebro (nueva_sesion / enviar)
       incluye `--agent arquitecto-cerebro` (ya NO usa el agente por defecto).
    2. El agente `arquitecto-cerebro` existe en ~/.config/opencode/agent/.
    3. El agente deniega bash.
    4. El agente deniega edicion/escritura.

NO toca los agentes de delegacion (arquitecto-lectura / arquitecto-escritura).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_RAIZ_ARQUI = Path(__file__).resolve().parent.parent
_RAIZ_AUTOS = _RAIZ_ARQUI.parent
for _p in (_RAIZ_ARQUI, _RAIZ_AUTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from comun import opencode  # noqa: E402

RUTA_AGENTE_CEREBRO = (
    Path.home() / ".config" / "opencode" / "agent" / "arquitecto-cerebro.md"
)
RUTA_AGENTE_LECTURA = (
    Path.home() / ".config" / "opencode" / "agent" / "arquitecto-lectura.md"
)
RUTA_AGENTE_ESCRITURA = (
    Path.home() / ".config" / "opencode" / "agent" / "arquitecto-escritura.md"
)


def _agente_consecutivo(comando: list[str], nombre: str) -> bool:
    """True si en `comando` aparece '--agent' inmediatamente seguido de `nombre`."""
    for i, tok in enumerate(comando[:-1]):
        if tok == "--agent" and comando[i + 1] == nombre:
            return True
    return False


def _frontmatter(ruta: Path) -> str:
    """Devuelve el bloque YAML de frontmatter (entre los dos primeros '---')."""
    texto = ruta.read_text(encoding="utf-8")
    partes = texto.split("---")
    # partes[0] suele ser vacio, partes[1] es el frontmatter.
    return partes[1] if len(partes) >= 3 else texto


# ============================================================================
# 1. El comando del cerebro usa --agent arquitecto-cerebro.
# ============================================================================


class TestSesionCerebroUsaAgenteRestringido(unittest.TestCase):

    def test_constante_agente_cerebro(self) -> None:
        self.assertEqual(opencode.AGENTE_CEREBRO, "arquitecto-cerebro")

    def test_nueva_sesion_incluye_agente_cerebro(self) -> None:
        fake = MagicMock(
            returncode=0,
            stdout='{"type":"step_start","sessionID":"ses_test",'
                   '"part":{"type":"step-start"}}\n',
            stderr="",
        )
        with patch("comun.opencode.disponible", return_value=True), \
             patch("comun.opencode.subprocess.run", return_value=fake) as mrun:
            sid = opencode.nueva_sesion()
        self.assertEqual(sid, "ses_test")
        comando = mrun.call_args.args[0]
        self.assertTrue(
            _agente_consecutivo(comando, "arquitecto-cerebro"),
            f"nueva_sesion no usa '--agent arquitecto-cerebro': {comando}",
        )

    def test_enviar_incluye_agente_cerebro(self) -> None:
        fake = MagicMock(
            returncode=0,
            stdout='{"type":"text","sessionID":"ses_x",'
                   '"part":{"type":"text","text":"hola"}}\n',
            stderr="",
        )
        with patch("comun.opencode.subprocess.run", return_value=fake) as mrun:
            txt = opencode.enviar("ses_x", "hola")
        self.assertEqual(txt, "hola")
        comando = mrun.call_args.args[0]
        self.assertTrue(
            _agente_consecutivo(comando, "arquitecto-cerebro"),
            f"enviar no usa '--agent arquitecto-cerebro': {comando}",
        )

    def test_cerebro_no_usa_agente_default(self) -> None:
        """La sesion del cerebro nunca corre con el agente por defecto."""
        fake = MagicMock(
            returncode=0,
            stdout='{"type":"text","sessionID":"ses_x",'
                   '"part":{"type":"text","text":"ok"}}\n',
            stderr="",
        )
        with patch("comun.opencode.subprocess.run", return_value=fake) as mrun:
            opencode.enviar("ses_x", "hola")
        comando = mrun.call_args.args[0]
        self.assertIn("--agent", comando,
                      "el comando del cerebro debe fijar un agente explicito")


# ============================================================================
# 2-4. El agente arquitecto-cerebro existe y restringe lo debido.
# ============================================================================


class TestAgenteCerebro(unittest.TestCase):

    def test_agente_existe(self) -> None:
        self.assertTrue(
            RUTA_AGENTE_CEREBRO.is_file(),
            f"falta el agente del cerebro en {RUTA_AGENTE_CEREBRO}",
        )

    def test_agente_deniega_bash(self) -> None:
        fm = _frontmatter(RUTA_AGENTE_CEREBRO)
        self.assertIn("bash: deny", fm, "el cerebro debe denegar bash en permission")
        self.assertIn("bash: false", fm, "el cerebro debe deshabilitar la tool bash")

    def test_agente_deniega_edicion_escritura(self) -> None:
        fm = _frontmatter(RUTA_AGENTE_CEREBRO)
        self.assertIn("edit: deny", fm, "el cerebro debe denegar edit")
        self.assertIn("write: deny", fm, "el cerebro debe denegar write")
        # Y tambien a nivel de tools (defensa en profundidad).
        self.assertIn("edit: false", fm)
        self.assertIn("write: false", fm)

    def test_agente_deniega_filesystem_lectura(self) -> None:
        """El cerebro tampoco lee ficheros: razona solo sobre el prompt."""
        fm = _frontmatter(RUTA_AGENTE_CEREBRO)
        self.assertIn("read: false", fm)


# ============================================================================
# 5. No se han tocado los agentes de delegacion (siguen existiendo).
# ============================================================================


class TestAgentesDelegacionIntactos(unittest.TestCase):

    def test_agentes_delegacion_presentes(self) -> None:
        if not RUTA_AGENTE_LECTURA.is_file():
            self.skipTest("entorno sin agentes de delegacion instalados")
        self.assertTrue(RUTA_AGENTE_LECTURA.is_file())
        self.assertTrue(RUTA_AGENTE_ESCRITURA.is_file())


if __name__ == "__main__":
    unittest.main()
