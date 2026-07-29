#!/usr/bin/env python3
"""
Tests de la Fase P1.2 — Confinamiento de `delegar_opencode` (escritura).

Verifica que la escritura delegada queda confinada a `~/arqui-sandbox`:
    - dentro del sandbox => permitida (con confirmacion);
    - rutas a /tmp, /etc o HOME fuera del sandbox => bloqueadas;
    - rutas con '..' que escapan => bloqueadas;
    - symlink dentro del sandbox que apunta fuera => bloqueado (deteccion);
    - confirmacion negativa => no ejecuta;
    - la traza marca delegar_opencode como excepcion fuera de manifiestos.

NO toca el cerebro, su agente, ni la logica de manifiestos.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_RAIZ_ARQUI = Path(__file__).resolve().parent.parent
_RAIZ_AUTOS = _RAIZ_ARQUI.parent
for _p in (_RAIZ_ARQUI, _RAIZ_AUTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from arquitecto import seguridad, ejecutor, trazas  # noqa: E402


def _decision(ambito: str, tarea: str) -> dict:
    return {
        "decision": "delegar_opencode",
        "ambito": ambito,
        "tarea": tarea,
        "razon": "test",
        "requiere_confirmacion": True,
    }


SANDBOX = seguridad.sandbox_escritura()


# ============================================================================
# 1. Escritura DENTRO del sandbox => permitida con confirmacion.
# ============================================================================


class TestEscrituraDentroDelSandbox(unittest.TestCase):

    def test_tarea_sin_rutas_permitida(self) -> None:
        v = seguridad.evaluar_delegacion(_decision("escritura", "crea un README"))
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)

    def test_ruta_dentro_del_sandbox_permitida(self) -> None:
        tarea = f"crea el fichero {SANDBOX}/nota.txt con un saludo"
        v = seguridad.evaluar_delegacion(_decision("escritura", tarea))
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_lectura_permitida(self) -> None:
        v = seguridad.evaluar_delegacion(_decision("lectura", "analiza el proyecto"))
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)


# ============================================================================
# 2-3. Escritura FUERA del sandbox => bloqueada.
# ============================================================================


class TestEscrituraFueraDelSandbox(unittest.TestCase):

    def test_tmp_bloqueada(self) -> None:
        v = seguridad.evaluar_delegacion(
            _decision("escritura", "escribe el log en /tmp/salida.txt"))
        self.assertFalse(v.permitido)
        self.assertIn("fuera del sandbox", v.motivo_bloqueo or "")

    def test_etc_bloqueada(self) -> None:
        v = seguridad.evaluar_delegacion(
            _decision("escritura", "modifica /etc/passwd para anadir un user"))
        self.assertFalse(v.permitido)

    def test_home_fuera_de_sandbox_bloqueada(self) -> None:
        v = seguridad.evaluar_delegacion(
            _decision("escritura", "guarda esto en ~/notas-secretas.txt"))
        self.assertFalse(v.permitido)

    def test_home_absoluto_fuera_de_sandbox_bloqueada(self) -> None:
        objetivo = str(Path.home() / "config-importante.cfg")
        v = seguridad.evaluar_delegacion(
            _decision("escritura", f"sobrescribe {objetivo}"))
        self.assertFalse(v.permitido)

    def test_dotdot_que_escapa_bloqueada(self) -> None:
        v = seguridad.evaluar_delegacion(
            _decision("escritura", "escribe en ../../etc/hosts una linea"))
        self.assertFalse(v.permitido)


# ============================================================================
# 4. Symlink dentro del sandbox apuntando fuera => detectado/bloqueado.
# ============================================================================


class TestDeteccionSymlinks(unittest.TestCase):

    def test_symlinks_que_escapan_detecta(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "dentro.txt").write_text("ok", encoding="utf-8")
            enlace = base / "fuga"
            try:
                enlace.symlink_to("/etc")
            except OSError:
                self.skipTest("no se pueden crear symlinks en este entorno")
            malos = seguridad.symlinks_que_escapan(base)
            self.assertIn(str(enlace), malos)

    def test_symlink_interno_no_se_marca(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "real.txt").write_text("ok", encoding="utf-8")
            enlace = base / "alias"
            try:
                enlace.symlink_to(base / "real.txt")
            except OSError:
                self.skipTest("no se pueden crear symlinks en este entorno")
            self.assertEqual(seguridad.symlinks_que_escapan(base), [])

    def test_evaluar_delegacion_bloquea_sandbox_con_symlink_fuga(self) -> None:
        # Sandbox temporal DENTRO de HOME (para pasar el chequeo de HOME) con
        # un symlink que escapa: evaluar_delegacion debe bloquear.
        tmp = Path(tempfile.mkdtemp(dir=Path.home(), prefix=".p12_test_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        enlace = tmp / "fuga"
        try:
            enlace.symlink_to("/etc")
        except OSError:
            self.skipTest("no se pueden crear symlinks en este entorno")
        with patch.object(seguridad, "SANDBOX_ESCRITURA", str(tmp)):
            v = seguridad.evaluar_delegacion(_decision("escritura", "crea algo"))
        self.assertFalse(v.permitido)
        self.assertIn("symlink", (v.motivo_bloqueo or "").lower())


# ============================================================================
# 5. Confirmacion negativa => no ejecuta.
# ============================================================================


class TestConfirmacionObligatoria(unittest.TestCase):

    def test_confirmacion_negativa_no_ejecuta(self) -> None:
        with patch("arquitecto.ejecutor.opencode.delegar") as mock_deleg:
            res = ejecutor.delegar_a_opencode(
                _decision("escritura", "crea un README"),
                confirmador=lambda _t: False,
            )
        mock_deleg.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("no confirmo", (res.motivo_no_ejecucion or "").lower())

    def test_sin_confirmador_no_ejecuta(self) -> None:
        with patch("arquitecto.ejecutor.opencode.delegar") as mock_deleg:
            res = ejecutor.delegar_a_opencode(
                _decision("escritura", "crea un README"),
                confirmador=None,
            )
        mock_deleg.assert_not_called()
        self.assertFalse(res.ejecutado)

    def test_ruta_fuera_no_ejecuta_ni_pregunta(self) -> None:
        """Si la tarea escapa del sandbox, se bloquea ANTES de confirmar."""
        confirmaciones = []

        def confirmador(texto):
            confirmaciones.append(texto)
            return True

        with patch("arquitecto.ejecutor.opencode.delegar") as mock_deleg:
            res = ejecutor.delegar_a_opencode(
                _decision("escritura", "escribe en /etc/hosts"),
                confirmador=confirmador,
            )
        mock_deleg.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertTrue(res.bloqueado)
        self.assertEqual(confirmaciones, [], "no debe pedir confirmacion si esta bloqueada")


# ============================================================================
# 6. Marca de excepcionalidad y traza.
# ============================================================================


class TestMarcaExcepcionalYTraza(unittest.TestCase):

    def test_confirmacion_marca_excepcional(self) -> None:
        v = seguridad.evaluar_delegacion(_decision("escritura", "crea un README"))
        self.assertIn("EXCEPCIONAL", v.texto_confirmacion)
        self.assertIn("fuera de manifiestos", v.texto_confirmacion)
        self.assertTrue(any("EXCEPCIONAL" in a for a in v.avisos))

    def test_traza_marca_delegacion_fuera_de_manifiestos(self) -> None:
        # Resultado tipico de una delegacion (clave sentinel 'opencode').
        res = ejecutor.ResultadoEjecucion(
            clave_automatizacion="opencode",
            nombre_operacion="delegar_escritura",
            comando=("opencode", "run"),
            ejecutado=True,
            codigo_salida=0,
        )
        traza = trazas.construir_traza(
            peticion_usuario="haz un refactor",
            decision="delegar_opencode",
            valida=True,
            requiere_confirmacion=True,
            resultados=[res],
        )
        self.assertEqual(traza["decision"], "delegar_opencode")
        self.assertEqual(len(traza["ejecuciones"]), 1)
        # 'opencode' como clave (no es una clave de manifiesto): inequivoco.
        self.assertEqual(
            traza["ejecuciones"][0]["clave_automatizacion"], "opencode")
        self.assertTrue(
            traza["ejecuciones"][0]["nombre_operacion"].startswith("delegar_"))


if __name__ == "__main__":
    unittest.main()
