"""
Tests de la fase PI-1 del Arquitecto del Castillo.

Anade al modo `delegar_ingenieria` el perfil `editar` (LECTURA + EDICION
CONFINADA): OpenCode lee y ademas edita/crea ficheros DENTRO de un directorio
de una raiz autorizada, con el agente `ingeniero-codigo`, SIN bash, SIN red y
SIN skills. La escritura queda confinada al directorio autorizado (mismo
modelo logico que `explorar`): editar fuera de la raiz se RECHAZA.

Alcance verificado:
  - el validador acepta `editar` (forma) y sigue rechazando `comandos`;
  - `ingenieria` mapea editar -> agente `ingeniero-codigo` y lo marca como
    perfil que escribe;
  - seguridad PERMITE `editar` dentro de una raiz autorizada (con
    confirmacion) y lo BLOQUEA fuera de la raiz o en rutas sensibles;
  - el ejecutor delega con el agente `ingeniero-codigo` y confina el `--dir`
    al directorio autorizado; sin confirmador no ejecuta; dry-run no delega;
  - el agente `ingeniero-codigo` NO tiene bash/web/skills (puede editar).

El subprocess de OpenCode se mockea (`comun.opencode.delegar`); las trazas
van a un fichero temporal.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_AQUI = Path(__file__).resolve()
_RAIZ_AUTOMATIZACIONES = _AQUI.parent.parent.parent
_PAQUETE_ARQUI = _AQUI.parent.parent
for _ruta in (_RAIZ_AUTOMATIZACIONES, _PAQUETE_ARQUI):
    if str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

from arquitecto import ejecutor, ingenieria, repl, seguridad, trazas  # noqa: E402
from arquitecto import validador  # noqa: E402
from arquitecto.cerebro import RespuestaCerebro  # noqa: E402


# -- Fabricas ------------------------------------------------------------------


def _decision(perfil: str = "editar", *, tarea: str = "edita el README",
              directorio: str | None = None, razon: str = "aplicar cambio",
              extra: dict | None = None) -> dict:
    d: dict = {
        "decision": "delegar_ingenieria",
        "tarea": tarea,
        "perfil": perfil,
        "razon": razon,
    }
    if directorio is not None:
        d["directorio"] = directorio
    if extra:
        d.update(extra)
    return d


def _raiz() -> Path:
    """Primera raiz autorizada (automatizaciones), garantizada en el entorno."""
    return ingenieria.raices_autorizadas()[0]


def _resp(norm: dict) -> RespuestaCerebro:
    return RespuestaCerebro(
        decision=str(norm.get("decision")), bruto=dict(norm),
        normalizada=dict(norm), valida=True, motivo_invalidez=None,
        reintentos=0,
        requiere_confirmacion=bool(norm.get("requiere_confirmacion", False)),
        turno_id="pi1",
    )


# -- 1. Validador (forma) ------------------------------------------------------


class TestValidadorEditar(unittest.TestCase):
    def _validar(self, decision: dict):
        return validador.validar_decision(decision, {})

    def test_editar_valida(self):
        ok, motivo, norm = self._validar(_decision())
        self.assertTrue(ok, motivo)
        self.assertEqual(norm["decision"], "delegar_ingenieria")
        self.assertEqual(norm["perfil"], "editar")
        self.assertTrue(norm["requiere_confirmacion"])

    def test_editar_con_directorio_valida(self):
        ok, motivo, norm = self._validar(
            _decision(directorio="~/Escritorio/automatizaciones")
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(norm["directorio"], "~/Escritorio/automatizaciones")

    def test_perfil_comandos_sigue_rechazado(self):
        ok, motivo, _ = self._validar(_decision(perfil="comandos"))
        self.assertFalse(ok)
        self.assertIn("perfil", motivo)

    def test_editar_en_enum_de_perfiles(self):
        self.assertIn("editar", validador._PERFILES_INGENIERIA)

    def test_tarea_demasiado_larga_rechazada(self):
        ok, _, _ = self._validar(_decision(tarea="x" * 801))
        self.assertFalse(ok)

    def test_falta_razon_rechazada(self):
        d = _decision()
        del d["razon"]
        ok, _, _ = self._validar(d)
        self.assertFalse(ok)


# -- 2. Modulo de politica `ingenieria` (perfil editar) ------------------------


class TestIngenieriaEditar(unittest.TestCase):
    def test_editar_mapea_a_ingeniero_codigo(self):
        self.assertEqual(
            ingenieria.PERFIL_AGENTE.get("editar"), "ingeniero-codigo",
        )

    def test_editar_es_perfil_que_escribe(self):
        self.assertTrue(ingenieria.perfil_escribe("editar"))

    def test_explorar_no_escribe(self):
        self.assertFalse(ingenieria.perfil_escribe("explorar"))

    def test_perfil_desconocido_no_escribe(self):
        self.assertFalse(ingenieria.perfil_escribe("comandos"))
        self.assertFalse(ingenieria.perfil_escribe(None))

    def test_resolver_ejecucion_editar_dentro_de_raiz(self):
        agente, directorio, motivo = ingenieria.resolver_ejecucion_ingenieria(
            _decision(directorio=str(_raiz())), _raiz(),
        )
        self.assertIsNone(motivo, motivo)
        self.assertEqual(agente, "ingeniero-codigo")
        self.assertEqual(directorio, _raiz())

    def test_resolver_ejecucion_editar_fuera_de_raiz_bloquea(self):
        agente, directorio, motivo = ingenieria.resolver_ejecucion_ingenieria(
            _decision(directorio="/tmp"), Path("/tmp"),
        )
        self.assertIsNone(agente)
        self.assertIsNone(directorio)
        self.assertIn("raices", motivo)


# -- 3. Seguridad: editar PERMITIDO dentro, BLOQUEADO fuera --------------------


class TestSeguridadEditar(unittest.TestCase):
    def test_editar_permitido_dentro_de_raiz(self):
        # (a) edicion permitida dentro de la raiz autorizada.
        v = seguridad.evaluar_ingenieria(_decision(), cwd=_raiz())
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)
        self.assertTrue(v.requiere_red)
        # El veredicto deja claro que es edicion confinada, no solo lectura.
        texto = v.texto_confirmacion.lower()
        self.assertIn("edici", texto)
        self.assertTrue(any("editar" in a.lower() for a in v.avisos))

    def test_editar_subdirectorio_de_raiz_permitido(self):
        sub = str(_raiz() / "el_arquitecto_del_castillo")
        v = seguridad.evaluar_ingenieria(
            _decision(directorio=sub), cwd=Path.cwd(),
        )
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_editar_fuera_de_raiz_bloqueado(self):
        # (b) bloqueo de edicion FUERA de la raiz autorizada.
        v = seguridad.evaluar_ingenieria(
            _decision(directorio="/tmp"), cwd=Path("/tmp"),
        )
        self.assertFalse(v.permitido)
        self.assertIn("raices", v.motivo_bloqueo)

    def test_editar_cwd_fuera_de_raiz_bloqueado(self):
        # Sin 'directorio' explicito y cwd fuera de raiz -> bloqueo.
        v = seguridad.evaluar_ingenieria(_decision(), cwd=Path("/tmp"))
        self.assertFalse(v.permitido)

    def test_editar_directorio_sensible_bloqueado(self):
        v = seguridad.evaluar_ingenieria(
            _decision(directorio="~/.ssh"), cwd=_raiz(),
        )
        self.assertFalse(v.permitido)

    def test_editar_escape_dotdot_bloqueado(self):
        # raiz/../../etc escapa de la raiz -> bloqueo.
        fuera = str(_raiz() / ".." / ".." / "etc")
        v = seguridad.evaluar_ingenieria(
            _decision(directorio=fuera), cwd=_raiz(),
        )
        self.assertFalse(v.permitido)

    def test_editar_tarea_con_ruta_sensible_bloqueada(self):
        v = seguridad.evaluar_ingenieria(
            _decision(tarea="edita ~/.ssh/id_rsa"), cwd=_raiz(),
        )
        self.assertFalse(v.permitido)
        self.assertIn("sensibles", v.motivo_bloqueo)


# -- 4. Agente ingeniero-codigo (lint del .md) ---------------------------------


class TestAgenteIngenieroCodigo(unittest.TestCase):
    def _texto(self) -> str:
        ruta = (
            Path.home() / ".config" / "opencode" / "agent"
            / "ingeniero-codigo.md"
        )
        self.assertTrue(ruta.is_file(), f"falta el agente {ruta}")
        return ruta.read_text(encoding="utf-8")

    def test_sin_bash_red_skill(self):
        t = self._texto()
        for denegado in (
            "bash: deny", "webfetch: deny", "websearch: deny", "skill: deny",
        ):
            self.assertIn(denegado, t, f"falta '{denegado}' en el agente")

    def test_bash_desactivada_como_tool(self):
        t = self._texto()
        self.assertIn("bash: false", t)

    def test_no_deniega_edicion(self):
        # A diferencia de ingeniero-lectura, este SI puede editar/escribir:
        # no debe haber edit/write/patch desactivados a nivel de linea.
        # (Se compara linea a linea con strip para no confundir 'write:'
        # con 'todowrite:'.)
        lineas = {ln.strip() for ln in self._texto().splitlines()}
        for prohibido in (
            "edit: deny", "write: deny", "patch: deny",
            "edit: false", "write: false", "patch: false",
        ):
            self.assertNotIn(
                prohibido, lineas, f"no debe aparecer la linea '{prohibido}'",
            )


# -- 5. Ejecutor (perfil editar) -----------------------------------------------


class TestEjecutorEditar(unittest.TestCase):
    def test_sin_confirmador_no_ejecuta(self):
        res = ejecutor.delegar_ingenieria(
            _decision(), confirmador=None, cwd=_raiz(),
        )
        self.assertFalse(res.ejecutado)
        self.assertIn("confirmaci", res.motivo_no_ejecucion)

    def test_confirmador_negativo_no_ejecuta(self):
        res = ejecutor.delegar_ingenieria(
            _decision(), confirmador=lambda _t: False, cwd=_raiz(),
        )
        self.assertFalse(res.ejecutado)

    def test_dry_run_no_delega(self):
        with patch("comun.opencode.delegar") as mock_del:
            res = ejecutor.delegar_ingenieria(
                _decision(), confirmador=lambda _t: True,
                dry_run=True, cwd=_raiz(),
            )
        self.assertFalse(res.ejecutado)
        mock_del.assert_not_called()

    def test_dir_no_autorizado_bloqueado(self):
        # (b) intento de editar FUERA de la raiz -> bloqueado por el ejecutor.
        res = ejecutor.delegar_ingenieria(
            _decision(directorio="/tmp"), confirmador=lambda _t: True,
            cwd=Path("/tmp"),
        )
        self.assertFalse(res.ejecutado)
        self.assertTrue(res.bloqueado)

    def test_exito_delega_con_agente_codigo(self):
        # (a) edicion permitida dentro de la raiz -> delega con ingeniero-codigo.
        with patch("comun.opencode.delegar", return_value="he editado X") as m:
            res = ejecutor.delegar_ingenieria(
                _decision(), confirmador=lambda _t: True, cwd=_raiz(),
            )
        self.assertTrue(res.ejecutado)
        self.assertEqual(res.codigo_salida, 0)
        self.assertIn("editado", res.stdout)
        self.assertEqual(res.perfil_ingenieria, "editar")
        self.assertEqual(res.directorio_autorizado, str(_raiz()))
        _, kwargs = m.call_args
        self.assertEqual(kwargs["agente"], "ingeniero-codigo")
        # El --dir pasado a OpenCode confina la escritura a la raiz.
        self.assertEqual(kwargs["directorio"], str(_raiz()))

    def test_etiqueta_operacion(self):
        with patch("comun.opencode.delegar", return_value="ok"):
            res = ejecutor.delegar_ingenieria(
                _decision(), confirmador=lambda _t: True, cwd=_raiz(),
            )
        self.assertEqual(res.nombre_operacion, "ingenieria_editar")


# -- 6. Enrutado del REPL (perfil editar) --------------------------------------


class TestReplEnrutadoEditar(unittest.TestCase):
    def test_procesar_respuesta_enruta_editar(self):
        ok, norm = validador.validar_decision(_decision(), {})[0::2]
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trazas.jsonl"
            with patch(
                "arquitecto.ejecutor.delegar_ingenieria"
            ) as mock_del:
                mock_del.return_value = ejecutor.ResultadoEjecucion(
                    clave_automatizacion="opencode",
                    nombre_operacion="ingenieria_editar",
                    comando=("opencode",), ejecutado=True, codigo_salida=0,
                    stdout="ok", perfil_ingenieria="editar",
                )
                resultado = repl.procesar_respuesta(
                    _resp(norm), {}, confirmador=lambda _t: True,
                    ruta_trazas=ruta, peticion_usuario="edita",
                )
            mock_del.assert_called_once()
            self.assertEqual(resultado.decision, "delegar_ingenieria")
            self.assertTrue(resultado.ejecuto_algo)


# -- 7. Trazas (perfil editar) -------------------------------------------------


class TestTrazasEditar(unittest.TestCase):
    def test_metadatos_perfil_editar_en_traza(self):
        res = ejecutor.ResultadoEjecucion(
            clave_automatizacion="opencode",
            nombre_operacion="ingenieria_editar",
            comando=("opencode",), ejecutado=True, codigo_salida=0,
            perfil_ingenieria="editar",
            directorio_autorizado=str(_raiz()),
        )
        traza = trazas.construir_traza(
            peticion_usuario="edita", decision="delegar_ingenieria",
            valida=True, resultados=[res],
        )
        self.assertTrue(traza["fuera_de_manifiestos"])
        ejec = traza["ejecuciones"][0]
        self.assertEqual(ejec["perfil_ingenieria"], "editar")
        self.assertEqual(ejec["directorio_autorizado"], str(_raiz()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
