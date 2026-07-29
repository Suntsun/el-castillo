"""
Tests de la fase PI-2 del Arquitecto del Castillo: decision `ejecutar_comandos`.

Modelo de seguridad verificado (OpenCode NO ejecuta bash):
  - el cerebro solo PROPONE comandos estructurados (binario + argumentos);
  - el Arquitecto valida contra su allowlist propia (`comandos.py`);
  - ejecuta el mismo con `subprocess.run([...], shell=False)`, entorno saneado,
    stdin cerrado, cwd confinado y timeout;
  - confirmacion humana UNICA para todo el lote;
  - SOLO LECTURA: nada de bash/sh/python -c, nada de mutadores, nada de
    git push/clean/reset, npm/pip install ni systemctl start/stop/restart;
  - lote de 1..5, validado al completo antes de ejecutar nada (all-or-nothing);
  - ejecucion en orden con STOP-ON-FAIL;
  - trazas por comando, marcadas `fuera_de_manifiestos`.

Cobertura: allowlist, denylist, rutas/confinamiento, lote, confirmacion,
ejecucion (shell=False/env/stdin), trazas, enrutado del REPL y NO regresion.
"""

from __future__ import annotations

import subprocess
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

from arquitecto import comandos, ejecutor, repl, seguridad, trazas  # noqa: E402
from arquitecto import validador  # noqa: E402
from arquitecto.cerebro import RespuestaCerebro  # noqa: E402


# -- Fabricas ------------------------------------------------------------------


def _cmd(binario: str, argumentos: list | None = None, *,
         directorio: str | None = None, razon: str | None = None) -> dict:
    c: dict = {"binario": binario, "argumentos": argumentos or []}
    if directorio is not None:
        c["directorio"] = directorio
    if razon is not None:
        c["razon"] = razon
    return c


def _decision(comandos_lista: list | None = None, *, razon: str = "inspeccion",
              extra: dict | None = None) -> dict:
    d: dict = {
        "decision": "ejecutar_comandos",
        "razon": razon,
        "comandos": comandos_lista if comandos_lista is not None
        else [_cmd("whoami")],
    }
    if extra:
        d.update(extra)
    return d


def _raiz() -> Path:
    return comandos.directorio_base_por_defecto()


def _validar(decision: dict):
    return validador.validar_decision(decision, {})


def _norm(decision: dict) -> dict:
    ok, motivo, norm = _validar(decision)
    assert ok, motivo
    return norm


def _resp(norm: dict) -> RespuestaCerebro:
    return RespuestaCerebro(
        decision=str(norm.get("decision")), bruto=dict(norm),
        normalizada=dict(norm), valida=True, motivo_invalidez=None,
        reintentos=0,
        requiere_confirmacion=bool(norm.get("requiere_confirmacion", False)),
        turno_id="pi2",
    )


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# -- 1. Validador (forma): allowlist y lote ------------------------------------


class TestValidadorComandos(unittest.TestCase):
    def test_lote_minimo_valido(self):
        ok, motivo, norm = _validar(_decision([_cmd("uname", ["-a"])]))
        self.assertTrue(ok, motivo)
        self.assertEqual(norm["decision"], "ejecutar_comandos")
        self.assertTrue(norm["requiere_confirmacion"])
        self.assertEqual(norm["comandos"][0]["binario"], "uname")

    def test_lote_varios_validos(self):
        ok, motivo, norm = _validar(_decision([
            _cmd("whoami"), _cmd("uname", ["-r"]), _cmd("df", ["-h"]),
        ]))
        self.assertTrue(ok, motivo)
        self.assertEqual(len(norm["comandos"]), 3)

    def test_en_decisiones_validas(self):
        self.assertIn("ejecutar_comandos", validador.DECISIONES_VALIDAS)

    def test_razon_obligatoria(self):
        d = _decision()
        del d["razon"]
        ok, _, _ = _validar(d)
        self.assertFalse(ok)

    def test_comandos_no_lista_rechazado(self):
        ok, _, _ = _validar(_decision(extra={"comandos": "whoami"}))
        self.assertFalse(ok)

    def test_lote_vacio_rechazado(self):
        ok, _, _ = _validar(_decision([]))
        self.assertFalse(ok)

    def test_lote_demasiado_grande_rechazado(self):
        ok, motivo, _ = _validar(_decision([_cmd("whoami")] * 6))
        self.assertFalse(ok)
        self.assertIn("entre 1 y 5", motivo)

    def test_max_comandos_es_cinco(self):
        ok, _, _ = _validar(_decision([_cmd("whoami")] * 5))
        self.assertTrue(ok)

    def test_campo_extra_en_decision_rechazado(self):
        ok, _, _ = _validar(_decision(extra={"foo": 1}))
        self.assertFalse(ok)

    def test_campo_extra_en_comando_rechazado(self):
        c = _cmd("whoami")
        c["foo"] = 1
        ok, _, _ = _validar(_decision([c]))
        self.assertFalse(ok)

    def test_binario_vacio_rechazado(self):
        ok, _, _ = _validar(_decision([_cmd("")]))
        self.assertFalse(ok)

    def test_argumentos_no_lista_rechazado(self):
        ok, _, _ = _validar(_decision([{"binario": "ls", "argumentos": "x"}]))
        self.assertFalse(ok)

    def test_argumento_no_string_rechazado(self):
        ok, _, _ = _validar(_decision([_cmd("ls", [123])]))
        self.assertFalse(ok)


# -- 2. Denylist: binarios y subcomandos prohibidos ----------------------------


class TestDenylistBinarios(unittest.TestCase):
    def _rechaza(self, binario, args=None):
        ok, motivo, _ = _validar(_decision([_cmd(binario, args or [])]))
        self.assertFalse(ok, f"{binario} deberia rechazarse")
        return motivo

    def test_bash_sh_python_rechazados(self):
        for b in ("bash", "sh", "zsh", "python", "python3", "perl", "ruby",
                  "node", "awk", "sed", "env", "xargs", "eval"):
            self._rechaza(b, ["-c", "echo hola"])

    def test_mutadores_rechazados(self):
        for b in ("rm", "mv", "cp", "chmod", "chown", "kill", "pkill",
                  "truncate", "dd", "mkfs", "ln", "touch", "tee"):
            self._rechaza(b, ["x"])

    def test_gestores_paquetes_rechazados(self):
        for b in ("npm", "pip", "pip3", "yarn", "pacman", "yay", "apt"):
            self._rechaza(b, ["install", "x"])

    def test_privilegios_rechazados(self):
        for b in ("sudo", "su", "doas", "pkexec"):
            self._rechaza(b, ["ls"])

    def test_find_rechazado_no_en_allowlist(self):
        # `find` se excluye en v1 (riesgo -exec/-delete); se usa fd/rg.
        self._rechaza("find", [".", "-name", "x"])


class TestDenylistGit(unittest.TestCase):
    def _rechaza_git(self, args):
        ok, motivo, _ = _validar(_decision([_cmd("git", args)]))
        self.assertFalse(ok, f"git {args} deberia rechazarse")
        return motivo

    def test_git_mutadores_rechazados(self):
        for sub in ("push", "pull", "fetch", "commit", "add", "reset",
                    "checkout", "switch", "restore", "clean", "rm", "mv",
                    "merge", "rebase", "stash", "config", "clone", "init",
                    "apply", "cherry-pick", "revert", "gc", "submodule",
                    "worktree", "bisect"):
            self._rechaza_git([sub])

    def test_git_push_explicito(self):
        m = self._rechaza_git(["push", "origin", "main"])
        self.assertIn("push", m)

    def test_git_clean_reset_explicitos(self):
        self._rechaza_git(["clean", "-fdx"])
        self._rechaza_git(["reset", "--hard"])

    def test_git_flag_c_antes_de_subcomando_rechazado(self):
        # `git -c core.pager=cmd status`: doble barrera -> se rechaza por flag
        # prohibido `-c` (inyeccion de config) o por flag antes del subcomando.
        m = self._rechaza_git(["-c", "core.pager=evil", "status"])
        self.assertIn("-c", m)

    def test_git_lectura_permitida(self):
        for args in (["status"], ["log", "--oneline"], ["diff", "--stat"],
                     ["show"], ["branch"], ["remote", "-v"], ["rev-parse", "HEAD"]):
            ok, motivo, _ = _validar(_decision([_cmd("git", args)]))
            self.assertTrue(ok, f"git {args}: {motivo}")

    def test_git_no_pager_inyectado(self):
        ok, _, argv = comandos.validar_forma_comando("git", ["status", "-s"])
        self.assertTrue(ok)
        self.assertEqual(argv, ["git", "--no-pager", "status", "-s"])


class TestDenylistSystemd(unittest.TestCase):
    def _rechaza(self, binario, args):
        ok, _, _ = _validar(_decision([_cmd(binario, args)]))
        self.assertFalse(ok)

    def test_systemctl_mutadores_rechazados(self):
        for sub in ("start", "stop", "restart", "reload", "enable", "disable",
                    "mask", "unmask", "daemon-reload", "kill", "isolate",
                    "set-default", "edit"):
            self._rechaza("systemctl", [sub, "x"])

    def test_systemctl_lectura_permitida(self):
        for args in (["status", "cronista_errores"], ["is-active", "x"],
                     ["list-timers"], ["list-units"], ["show", "x"]):
            ok, motivo, _ = _validar(_decision([_cmd("systemctl", args)]))
            self.assertTrue(ok, f"systemctl {args}: {motivo}")

    def test_systemctl_user_y_no_pager_inyectados(self):
        ok, _, argv = comandos.validar_forma_comando("systemctl", ["status", "x"])
        self.assertTrue(ok)
        self.assertEqual(argv[:3], ["systemctl", "--user", "--no-pager"])

    def test_systemctl_system_flag_prohibido(self):
        ok, _, _ = comandos.validar_forma_comando(
            "systemctl", ["status", "--system"],
        )
        self.assertFalse(ok)

    def test_journalctl_follow_prohibido(self):
        for flag in ("-f", "--follow"):
            ok, _, _ = comandos.validar_forma_comando("journalctl", [flag])
            self.assertFalse(ok, f"journalctl {flag} deberia rechazarse")

    def test_journalctl_user_dedup(self):
        # El cerebro ya incluye --user: no debe duplicarse.
        ok, _, argv = comandos.validar_forma_comando(
            "journalctl", ["--user", "-n", "5"],
        )
        self.assertTrue(ok)
        self.assertEqual(argv.count("--user"), 1)
        self.assertIn("--no-pager", argv)


class TestDenylistFlags(unittest.TestCase):
    def test_tail_follow_prohibido(self):
        for flag in ("-f", "-F", "--follow"):
            ok, _, _ = comandos.validar_forma_comando("tail", [flag, "x"])
            self.assertFalse(ok, f"tail {flag}")

    def test_fd_exec_prohibido(self):
        for flag in ("-x", "--exec", "-X", "--exec-batch"):
            ok, _, _ = comandos.validar_forma_comando("fd", [flag, "rm"])
            self.assertFalse(ok, f"fd {flag}")

    def test_rg_pre_prohibido(self):
        ok, _, _ = comandos.validar_forma_comando("rg", ["--pre", "evil", "x"])
        self.assertFalse(ok)

    def test_find_exec_global_prohibido_en_cualquier_binario(self):
        # Aunque el binario estuviera permitido, -exec/-delete se vetan.
        ok, _, _ = comandos.validar_forma_comando("ls", ["-exec", "rm"])
        self.assertFalse(ok)
        ok, _, _ = comandos.validar_forma_comando("du", ["-delete"])
        self.assertFalse(ok)


# -- 3. Metacaracteres, '..', NUL y limites ------------------------------------


class TestSaneamientoArgumentos(unittest.TestCase):
    def test_metacaracteres_rechazados(self):
        for arg in ("a;b", "a|b", "a&b", "a`b", "a$b", "a>b", "a<b", "a\\b"):
            ok, _, _ = comandos.validar_forma_comando("grep", [arg, "."])
            self.assertFalse(ok, f"meta {arg!r} deberia rechazarse")

    def test_salto_de_linea_rechazado(self):
        ok, _, _ = comandos.validar_forma_comando("grep", ["a\nb"])
        self.assertFalse(ok)

    def test_dotdot_rechazado(self):
        for arg in ("../x", "a/../b", "~/repos/../..", "/home/sun/.."):
            ok, _, _ = comandos.validar_forma_comando("cat", [arg])
            self.assertFalse(ok, f"'..' en {arg!r} deberia rechazarse")

    def test_nul_rechazado(self):
        ok, _, _ = comandos.validar_forma_comando("cat", ["a\x00b"])
        self.assertFalse(ok)

    def test_argumento_demasiado_largo_rechazado(self):
        ok, _, _ = comandos.validar_forma_comando("cat", ["x" * 300])
        self.assertFalse(ok)

    def test_demasiados_argumentos_rechazado(self):
        ok, _, _ = comandos.validar_forma_comando("ls", ["-l"] * 30)
        self.assertFalse(ok)


# -- 4. Confinamiento de rutas (seguridad) -------------------------------------


class TestConfinamientoRutas(unittest.TestCase):
    def _seg(self, comandos_lista, cwd=None):
        norm = _norm(_decision(comandos_lista))
        return seguridad.evaluar_comandos(norm, cwd=cwd or _raiz())

    def test_ruta_dentro_de_raiz_permitida(self):
        v = self._seg([_cmd("cat", [str(_raiz() / "HANDOFF.md")])])
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_etc_passwd_bloqueado(self):
        v = self._seg([_cmd("cat", ["/etc/passwd"])])
        self.assertFalse(v.permitido)
        self.assertIn("sensible", v.motivo_bloqueo)

    def test_ssh_privada_bloqueada(self):
        v = self._seg([_cmd("cat", ["~/.ssh/id_rsa"])])
        self.assertFalse(v.permitido)

    def test_ruta_absoluta_fuera_de_raices_bloqueada(self):
        v = self._seg([_cmd("ls", ["/var/log"])])
        self.assertFalse(v.permitido)
        self.assertIn("raices", v.motivo_bloqueo)

    def test_ruta_relativa_permitida(self):
        # Relativa sin '..' resuelve bajo la raiz autorizada (cwd) -> OK.
        v = self._seg([_cmd("ls", ["el_arquitecto_del_castillo"])])
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_directorio_no_autorizado_bloqueado(self):
        v = self._seg([_cmd("ls", [], directorio="/tmp")], cwd=Path("/tmp"))
        self.assertFalse(v.permitido)

    def test_directorio_sensible_bloqueado(self):
        v = self._seg([_cmd("ls", [], directorio="~/.gnupg")])
        self.assertFalse(v.permitido)

    def test_du_raiz_sistema_bloqueada(self):
        v = self._seg([_cmd("du", ["-sh", "/"])])
        self.assertFalse(v.permitido)


# -- 5. Modulo comandos: resolutores y entorno ---------------------------------


class TestModuloComandos(unittest.TestCase):
    def test_max_comandos_constante(self):
        self.assertEqual(comandos.MAX_COMANDOS, 5)

    def test_binario_permitido(self):
        self.assertIsNotNone(comandos.binario_permitido("git"))
        self.assertIsNone(comandos.binario_permitido("bash"))
        self.assertIsNone(comandos.binario_permitido(None))

    def test_resolver_binario_sistema(self):
        ruta, motivo = comandos.resolver_binario("git")
        self.assertIsNone(motivo, motivo)
        self.assertTrue(ruta.startswith("/"))
        # No debe colgar de HOME (anti-shadowing).
        self.assertFalse(ruta.startswith(str(Path.home())))

    def test_resolver_binario_no_permitido(self):
        ruta, motivo = comandos.resolver_binario("bash")
        self.assertIsNone(ruta)
        self.assertIsNotNone(motivo)

    def test_entorno_seguro_minimo(self):
        env = comandos.entorno_seguro()
        self.assertEqual(env["PATH"], comandos.PATH_SEGURO)
        self.assertEqual(env["GIT_PAGER"], "cat")
        self.assertNotIn("LD_PRELOAD", env)

    def test_preparar_lote_all_or_nothing(self):
        # Un comando malo en el lote bloquea TODO el lote.
        prep, motivo = comandos.preparar_lote(
            [_cmd("whoami"), _cmd("cat", ["/etc/shadow"])], cwd_base=_raiz(),
        )
        self.assertEqual(prep, [])
        self.assertIsNotNone(motivo)
        self.assertIn("#2", motivo)


# -- 6. Seguridad: veredicto del lote ------------------------------------------


class TestSeguridadComandos(unittest.TestCase):
    def test_lote_valido_permitido_y_confirma(self):
        norm = _norm(_decision([_cmd("whoami"), _cmd("uname", ["-a"])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)
        self.assertFalse(v.requiere_red)  # v1 sin red
        self.assertIn("solo lectura", v.texto_confirmacion.lower())
        # El texto muestra el argv exacto de cada comando.
        self.assertIn("$ whoami", v.texto_confirmacion)

    def test_aviso_fuera_de_manifiestos(self):
        norm = _norm(_decision())
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(any("fuera de manifiestos" in a.lower() for a in v.avisos))


# -- 7. Ejecutor: confirmacion, ejecucion, shell=False, stop-on-fail -----------


class TestEjecutorComandos(unittest.TestCase):
    def test_sin_confirmador_no_ejecuta(self):
        lote = ejecutor.ejecutar_comandos(_norm(_decision()), confirmador=None,
                                          cwd=_raiz())
        self.assertFalse(lote.exito)
        self.assertFalse(lote.resultados[0].ejecutado)

    def test_confirmador_negativo_no_ejecuta(self):
        lote = ejecutor.ejecutar_comandos(_norm(_decision()),
                                          confirmador=lambda _t: False,
                                          cwd=_raiz())
        self.assertFalse(any(r.ejecutado for r in lote.resultados))

    def test_confirmacion_unica_para_el_lote(self):
        llamadas = {"n": 0}

        def conf(_t):
            llamadas["n"] += 1
            return True

        norm = _norm(_decision([_cmd("whoami"), _cmd("uname"), _cmd("id")]))
        lote = ejecutor.ejecutar_comandos(norm, confirmador=conf, cwd=_raiz())
        self.assertEqual(llamadas["n"], 1, "una sola confirmacion para el lote")
        self.assertTrue(lote.exito, [r.error for r in lote.resultados])

    def test_ejecucion_real_captura_salida(self):
        norm = _norm(_decision([_cmd("whoami")]))
        lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                          cwd=_raiz())
        self.assertTrue(lote.exito)
        self.assertEqual(lote.resultados[0].stdout.strip(),
                         comandos.entorno_seguro()["USER"] or
                         lote.resultados[0].stdout.strip())

    def test_dry_run_no_ejecuta(self):
        norm = _norm(_decision([_cmd("whoami"), _cmd("uname")]))
        with patch("arquitecto.ejecutor.subprocess.run") as m:
            lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                              dry_run=True, cwd=_raiz())
        m.assert_not_called()
        self.assertFalse(any(r.ejecutado for r in lote.resultados))
        # El dry-run muestra el argv exacto.
        self.assertEqual(lote.resultados[0].comando, ("whoami",))

    def test_subprocess_shell_false_env_y_stdin(self):
        norm = _norm(_decision([_cmd("whoami")]))
        with patch("arquitecto.ejecutor.subprocess.run",
                   return_value=_FakeProc(0, "sun\n", "")) as m:
            ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                       cwd=_raiz())
        _args, kwargs = m.call_args
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["PATH"], comandos.PATH_SEGURO)
        self.assertEqual(kwargs["cwd"], str(_raiz()))
        self.assertEqual(kwargs["timeout"], comandos.TIMEOUT_COMANDO_S)
        # El primer token es la RUTA ABSOLUTA resuelta, no el nombre.
        argv_real = _args[0]
        self.assertTrue(argv_real[0].startswith("/"))

    def test_stop_on_fail(self):
        # ls de algo inexistente (exit!=0) detiene el lote antes del whoami.
        norm = _norm(_decision([_cmd("ls", ["no_existe_zzz_123"]), _cmd("whoami")]))
        lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                          cwd=_raiz())
        self.assertTrue(lote.abortada)
        self.assertEqual(lote.comando_fallido, 0)
        self.assertEqual(len(lote.resultados), 1)  # el whoami no se ejecuto

    def test_lote_bloqueado_no_ejecuta_nada(self):
        # Una ruta sensible bloquea el lote entero en seguridad.
        norm = _norm(_decision([_cmd("whoami"), _cmd("cat", ["/etc/shadow"])]))
        with patch("arquitecto.ejecutor.subprocess.run") as m:
            lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                              cwd=_raiz())
        m.assert_not_called()
        self.assertTrue(lote.bloqueado)
        self.assertFalse(lote.exito)

    def test_binario_no_encontrado_se_refleja(self):
        norm = _norm(_decision([_cmd("git", ["status"])]))
        with patch("arquitecto.comandos.resolver_binario",
                   return_value=(None, "binario 'git' no encontrado")):
            lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                              cwd=_raiz())
        self.assertFalse(lote.resultados[0].ejecutado)
        self.assertIn("no encontrado", lote.resultados[0].motivo_no_ejecucion)

    def test_timeout_se_refleja(self):
        norm = _norm(_decision([_cmd("whoami")]))
        with patch("arquitecto.ejecutor.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="whoami", timeout=30)):
            lote = ejecutor.ejecutar_comandos(norm, confirmador=lambda _t: True,
                                              cwd=_raiz())
        self.assertTrue(lote.resultados[0].timeout)
        self.assertFalse(lote.exito)


# -- 8. Enrutado del REPL ------------------------------------------------------


class TestReplEnrutadoComandos(unittest.TestCase):
    def test_procesar_respuesta_enruta_comandos(self):
        norm = _norm(_decision([_cmd("whoami")]))
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trazas.jsonl"
            with patch("arquitecto.ejecutor.ejecutar_comandos") as m:
                m.return_value = ejecutor.ResultadoComandos(
                    razon="x",
                    resultados=(ejecutor.ResultadoEjecucion(
                        clave_automatizacion="comandos", nombre_operacion="whoami",
                        comando=("whoami",), ejecutado=True, codigo_salida=0,
                        stdout="sun",
                    ),),
                )
                resultado = repl.procesar_respuesta(
                    _resp(norm), {}, confirmador=lambda _t: True,
                    ruta_trazas=ruta, peticion_usuario="quien soy",
                )
            m.assert_called_once()
            self.assertEqual(resultado.decision, "ejecutar_comandos")
            self.assertTrue(resultado.ejecuto_algo)

    def test_repl_render_lote_abortado(self):
        norm = _norm(_decision([_cmd("ls", ["no_existe_zzz"]), _cmd("whoami")]))
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trazas.jsonl"
            resultado = repl.procesar_respuesta(
                _resp(norm), {}, confirmador=lambda _t: True,
                ruta_trazas=ruta, peticion_usuario="x",
            )
        self.assertTrue(any("stop-on-fail" in m for m in resultado.mensajes))


# -- 9. Trazas -----------------------------------------------------------------


class TestTrazasComandos(unittest.TestCase):
    def test_fuera_de_manifiestos(self):
        traza = trazas.construir_traza(
            peticion_usuario="x", decision="ejecutar_comandos", valida=True,
            resultados=[],
        )
        self.assertTrue(traza["fuera_de_manifiestos"])

    def test_traza_por_comando(self):
        res = [
            ejecutor.ResultadoEjecucion(
                clave_automatizacion="comandos", nombre_operacion="whoami",
                comando=("whoami",), ejecutado=True, codigo_salida=0),
            ejecutor.ResultadoEjecucion(
                clave_automatizacion="comandos", nombre_operacion="uname",
                comando=("uname", "-a"), ejecutado=True, codigo_salida=0),
        ]
        traza = trazas.construir_traza(
            peticion_usuario="x", decision="ejecutar_comandos", valida=True,
            resultados=res,
        )
        self.assertEqual(len(traza["ejecuciones"]), 2)
        self.assertEqual(traza["ejecuciones"][0]["nombre_operacion"], "whoami")
        self.assertEqual(traza["ejecuciones"][1]["nombre_operacion"], "uname")


# -- 10. No regresion ----------------------------------------------------------


class TestNoRegresion(unittest.TestCase):
    def test_delegar_ingenieria_comandos_sigue_rechazado(self):
        # El perfil `comandos` de delegar_ingenieria NO existe (era el PI-2 viejo).
        d = {"decision": "delegar_ingenieria", "tarea": "x",
             "perfil": "comandos", "razon": "y"}
        ok, _, _ = validador.validar_decision(d, {})
        self.assertFalse(ok)

    def test_responder_sigue_validando(self):
        ok, _, _ = validador.validar_decision(
            {"decision": "responder", "texto": "hola"}, {})
        self.assertTrue(ok)

    def test_decision_desconocida_rechazada(self):
        ok, _, _ = validador.validar_decision(
            {"decision": "ejecutar_bash", "x": 1}, {})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
