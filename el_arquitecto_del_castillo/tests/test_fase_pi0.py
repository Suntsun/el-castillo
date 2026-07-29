"""
Tests de la fase PI-0 del Arquitecto del Castillo.

Cubre la nueva decision `delegar_ingenieria` con perfil `explorar` (SOLO
lectura): modulo de politica `ingenieria`, validacion de forma, veredicto de
seguridad, ejecutor (sin lanzar OpenCode real), enrutado del REPL y traza.

Alcance verificado:
  - perfil 'explorar' es valido; 'comandos' (PI-2) se rechaza ('editar' paso
    a ser valido en PI-1, cubierto en test_fase_pi1.py);
  - raices autorizadas exactas; fuera -> bloqueo; '~/.config' entero -> bloqueo;
  - rutas sensibles (credenciales) -> bloqueo;
  - el agente `ingeniero-lectura` NO tiene bash/edit/write/web/skill.

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


def _decision(perfil: str = "explorar", *, tarea: str = "explora el repo",
              directorio: str | None = None, razon: str = "mirar ficheros",
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
        turno_id="pi0",
    )


# -- 1. Validador (forma) ------------------------------------------------------


class TestValidadorIngenieria(unittest.TestCase):
    def _validar(self, decision: dict):
        return validador.validar_decision(decision, {})

    def test_explorar_valida(self):
        ok, motivo, norm = self._validar(_decision())
        self.assertTrue(ok, motivo)
        self.assertEqual(norm["decision"], "delegar_ingenieria")
        self.assertEqual(norm["perfil"], "explorar")
        self.assertTrue(norm["requiere_confirmacion"])

    def test_explorar_con_directorio_valida(self):
        ok, motivo, norm = self._validar(
            _decision(directorio="~/Escritorio/automatizaciones")
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(norm["directorio"], "~/Escritorio/automatizaciones")

    # Nota: el perfil 'editar' paso a ser VALIDO en PI-1; su validacion vive
    # ahora en tests/test_fase_pi1.py. Aqui solo se conserva que 'comandos'
    # (PI-2, aun inexistente) sigue rechazado.

    def test_perfil_comandos_rechazado(self):
        ok, motivo, _ = self._validar(_decision(perfil="comandos"))
        self.assertFalse(ok)
        self.assertIn("perfil", motivo)

    def test_campos_extra_rechazados(self):
        ok, motivo, _ = self._validar(_decision(extra={"ambito": "escritura"}))
        self.assertFalse(ok)
        self.assertIn("extra", motivo)

    def test_tarea_vacia_rechazada(self):
        ok, _, _ = self._validar(_decision(tarea=""))
        self.assertFalse(ok)

    def test_tarea_demasiado_larga_rechazada(self):
        ok, _, _ = self._validar(_decision(tarea="x" * 801))
        self.assertFalse(ok)

    def test_falta_razon_rechazada(self):
        d = _decision()
        del d["razon"]
        ok, _, _ = self._validar(d)
        self.assertFalse(ok)

    def test_directorio_no_string_rechazado(self):
        ok, _, _ = self._validar(_decision(extra={"directorio": 123}))
        self.assertFalse(ok)

    def test_enum_incluye_delegar_ingenieria(self):
        self.assertIn("delegar_ingenieria", validador.DECISIONES_VALIDAS)


# -- 2. Modulo de politica `ingenieria` ----------------------------------------


class TestIngenieriaRaices(unittest.TestCase):
    def test_siete_raices_autorizadas(self):
        raices = ingenieria.raices_autorizadas()
        self.assertEqual(len(raices), 7)
        esperadas = {
            "automatizaciones", "proyectos", "repos",
            "omarchy", "waybar", "hypr", "walker",
        }
        nombres = {r.name for r in raices}
        self.assertEqual(nombres, esperadas)

    def test_cada_raiz_se_autoriza_a_si_misma(self):
        cwd = Path.cwd()
        for raiz in ingenieria.raices_autorizadas():
            d, motivo = ingenieria.resolver_directorio_autorizado(
                str(raiz), cwd,
            )
            self.assertIsNone(motivo, f"{raiz}: {motivo}")
            self.assertEqual(d, raiz)

    def test_subcarpeta_de_raiz_autorizada(self):
        sub = str(_raiz() / "el_arquitecto_del_castillo" / "arquitecto")
        d, motivo = ingenieria.resolver_directorio_autorizado(sub, Path.cwd())
        self.assertIsNone(motivo, motivo)
        self.assertEqual(d, Path(sub))

    def test_fuera_de_raices_bloqueado(self):
        d, motivo = ingenieria.resolver_directorio_autorizado(
            "/tmp", Path.cwd(),
        )
        self.assertIsNone(d)
        self.assertIn("fuera de las raices", motivo)

    def test_config_entero_no_autorizado(self):
        d, motivo = ingenieria.resolver_directorio_autorizado(
            "~/.config", Path.cwd(),
        )
        self.assertIsNone(d)
        self.assertIn("fuera de las raices", motivo)

    def test_config_subcarpeta_no_listada_bloqueada(self):
        # ~/.config/dunst NO esta en la whitelist (solo omarchy/waybar/...).
        d, motivo = ingenieria.resolver_directorio_autorizado(
            "~/.config/dunst", Path.cwd(),
        )
        self.assertIsNone(d)

    def test_dir_none_usa_cwd_autorizado(self):
        cwd = _raiz()
        d, motivo = ingenieria.resolver_directorio_autorizado(None, cwd)
        self.assertIsNone(motivo, motivo)
        self.assertEqual(d, cwd)

    def test_dir_none_cwd_no_autorizado_bloquea(self):
        d, motivo = ingenieria.resolver_directorio_autorizado(
            None, Path("/tmp"),
        )
        self.assertIsNone(d)

    def test_escape_dotdot_bloqueado(self):
        # ~/repos/../.ssh -> ~/.ssh (sensible y fuera de raices).
        d, motivo = ingenieria.resolver_directorio_autorizado(
            "~/repos/../.ssh", Path.cwd(),
        )
        self.assertIsNone(d)

    def test_directorio_sensible_bloqueado(self):
        d, motivo = ingenieria.resolver_directorio_autorizado(
            "~/.ssh", Path.cwd(),
        )
        self.assertIsNone(d)
        self.assertIn("sensible", motivo)


class TestIngenieriaSensibles(unittest.TestCase):
    def test_ssh_es_sensible(self):
        self.assertTrue(ingenieria.ruta_es_sensible(Path("~/.ssh/id_rsa")))

    def test_etc_es_sensible(self):
        self.assertTrue(ingenieria.ruta_es_sensible(Path("/etc/passwd")))

    def test_env_es_sensible(self):
        self.assertTrue(ingenieria.ruta_es_sensible(Path("~/repos/x/.env")))

    def test_repo_normal_no_sensible(self):
        self.assertFalse(
            ingenieria.ruta_es_sensible(Path("~/repos/proyecto/main.py"))
        )

    def test_rutas_sensibles_en_texto(self):
        encontradas = ingenieria.rutas_sensibles_en_texto(
            "lee el fichero /etc/shadow y dime que hay"
        )
        self.assertIn("/etc/shadow", encontradas)

    def test_texto_sin_rutas_sensibles(self):
        self.assertEqual(
            ingenieria.rutas_sensibles_en_texto("explora el repo y resume"),
            [],
        )


# -- 3. Seguridad (veredicto) --------------------------------------------------


class TestSeguridadIngenieria(unittest.TestCase):
    def test_explorar_permitido_con_confirmacion(self):
        v = seguridad.evaluar_ingenieria(_decision(), cwd=_raiz())
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)
        self.assertTrue(v.requiere_red)
        self.assertTrue(any("explorar" in a.lower() for a in v.avisos))

    def test_dir_fuera_de_raiz_bloqueado(self):
        v = seguridad.evaluar_ingenieria(_decision(), cwd=Path("/tmp"))
        self.assertFalse(v.permitido)
        self.assertIn("raices", v.motivo_bloqueo)

    def test_perfil_no_soportado_bloqueado(self):
        # Defensa en profundidad: aunque el validador rechaza antes.
        v = seguridad.evaluar_ingenieria(
            _decision(perfil="comandos"), cwd=_raiz(),
        )
        self.assertFalse(v.permitido)

    def test_tarea_con_ruta_sensible_bloqueada(self):
        v = seguridad.evaluar_ingenieria(
            _decision(tarea="lee ~/.ssh/id_rsa y resume"), cwd=_raiz(),
        )
        self.assertFalse(v.permitido)
        self.assertIn("sensibles", v.motivo_bloqueo)


# -- 4. Agente ingeniero-lectura (lint del .md) --------------------------------


class TestAgenteIngenieroLectura(unittest.TestCase):
    def _texto(self) -> str:
        ruta = (
            Path.home() / ".config" / "opencode" / "agent"
            / "ingeniero-lectura.md"
        )
        self.assertTrue(ruta.is_file(), f"falta el agente {ruta}")
        return ruta.read_text(encoding="utf-8")

    def test_sin_bash_edicion_red_skill(self):
        t = self._texto()
        for denegado in (
            "bash: deny", "edit: deny", "write: deny",
            "webfetch: deny", "websearch: deny", "skill: deny",
        ):
            self.assertIn(denegado, t, f"falta '{denegado}' en el agente")

    def test_tools_desactivadas(self):
        t = self._texto()
        for off in ("bash: false", "edit: false", "write: false"):
            self.assertIn(off, t)


# -- 5. Ejecutor ---------------------------------------------------------------


class TestEjecutorIngenieria(unittest.TestCase):
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
        res = ejecutor.delegar_ingenieria(
            _decision(), confirmador=lambda _t: True, cwd=Path("/tmp"),
        )
        self.assertFalse(res.ejecutado)
        self.assertTrue(res.bloqueado)

    def test_exito_delega_y_devuelve_texto(self):
        with patch("comun.opencode.delegar", return_value="estructura...") as m:
            res = ejecutor.delegar_ingenieria(
                _decision(), confirmador=lambda _t: True, cwd=_raiz(),
            )
        self.assertTrue(res.ejecutado)
        self.assertEqual(res.codigo_salida, 0)
        self.assertIn("estructura", res.stdout)
        self.assertEqual(res.perfil_ingenieria, "explorar")
        self.assertEqual(res.directorio_autorizado, str(_raiz()))
        # Se delego con el agente de SOLO LECTURA.
        _, kwargs = m.call_args
        self.assertEqual(kwargs["agente"], "ingeniero-lectura")

    def test_etiqueta_operacion(self):
        with patch("comun.opencode.delegar", return_value="ok"):
            res = ejecutor.delegar_ingenieria(
                _decision(), confirmador=lambda _t: True, cwd=_raiz(),
            )
        self.assertEqual(res.nombre_operacion, "ingenieria_explorar")


# -- 6. Enrutado del REPL ------------------------------------------------------


class TestReplEnrutado(unittest.TestCase):
    def test_procesar_respuesta_enruta_ingenieria(self):
        ok, norm = validador.validar_decision(_decision(), {})[0::2]
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trazas.jsonl"
            with patch(
                "arquitecto.ejecutor.delegar_ingenieria"
            ) as mock_del:
                mock_del.return_value = ejecutor.ResultadoEjecucion(
                    clave_automatizacion="opencode",
                    nombre_operacion="ingenieria_explorar",
                    comando=("opencode",), ejecutado=True, codigo_salida=0,
                    stdout="ok", perfil_ingenieria="explorar",
                )
                resultado = repl.procesar_respuesta(
                    _resp(norm), {}, confirmador=lambda _t: True,
                    ruta_trazas=ruta, peticion_usuario="explora",
                )
            mock_del.assert_called_once()
            self.assertEqual(resultado.decision, "delegar_ingenieria")
            self.assertTrue(resultado.ejecuto_algo)

    def test_dry_run_no_pide_confirmacion(self):
        norm = validador.validar_decision(_decision(), {})[2]
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trazas.jsonl"

            def _confirm(_t):
                raise AssertionError("no debe pedir confirmacion en dry-run")

            with patch("comun.opencode.delegar") as mock_del:
                # cwd real autorizado para que llegue al dry-run.
                with patch("pathlib.Path.cwd", return_value=_raiz()):
                    resultado = repl.procesar_respuesta(
                        _resp(norm), {}, confirmador=_confirm, dry_run=True,
                        ruta_trazas=ruta, peticion_usuario="explora",
                    )
            mock_del.assert_not_called()
            self.assertFalse(resultado.ejecuto_algo)


# -- 7. Trazas -----------------------------------------------------------------


class TestTrazasIngenieria(unittest.TestCase):
    def test_fuera_de_manifiestos_true(self):
        traza = trazas.construir_traza(
            peticion_usuario="explora", decision="delegar_ingenieria",
            valida=True,
        )
        self.assertTrue(traza["fuera_de_manifiestos"])

    def test_metadatos_perfil_en_traza(self):
        res = ejecutor.ResultadoEjecucion(
            clave_automatizacion="opencode",
            nombre_operacion="ingenieria_explorar",
            comando=("opencode",), ejecutado=True, codigo_salida=0,
            perfil_ingenieria="explorar",
            directorio_autorizado=str(_raiz()),
        )
        traza = trazas.construir_traza(
            peticion_usuario="explora", decision="delegar_ingenieria",
            valida=True, resultados=[res],
        )
        ejec = traza["ejecuciones"][0]
        self.assertEqual(ejec["perfil_ingenieria"], "explorar")
        self.assertEqual(ejec["directorio_autorizado"], str(_raiz()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
