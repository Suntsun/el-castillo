"""
Tests de la fase PI-2 del Arquitecto del Castillo: decision `ejecutar_comandos`.

Modelo de seguridad verificado (OpenCode NO ejecuta bash):
  - el cerebro solo PROPONE comandos estructurados (binario + argumentos);
  - el Arquitecto valida contra su ALLOWLIST DE FLAGS por binario (`comandos.
    py`): default-deny tambien en flags, no solo en binarios/subcomandos;
  - ejecuta el mismo con `subprocess.run([...], shell=False)`, entorno saneado,
    stdin cerrado, cwd confinado y timeout;
  - confirmacion humana UNICA para todo el lote;
  - SOLO LECTURA: nada de bash/sh/python -c, nada de mutadores, nada de
    git push/clean/reset, npm/pip install ni systemctl start/stop/restart;
  - lote de 1..5, validado al completo antes de ejecutar nada (all-or-nothing);
  - ejecucion en orden con STOP-ON-FAIL;
  - trazas por comando, marcadas `fuera_de_manifiestos`.

Cobertura: allowlist de binarios/subcomandos, ALLOWLIST DE FLAGS (con
normalizacion previa: abreviaturas de flags largos, desagrupado de flags
cortos pegados, separacion uniforme flag/valor), confinamiento de rutas
(posicionales y valores de flag), lote, confirmacion, ejecucion (shell=False/
env/stdin), trazas, enrutado del REPL y NO regresion.

Nota sobre el REDISEÑO (denylist -> whitelist de flags, ver docstring de
`comandos.py`): dos auditorias adversariales demostraron que una denylist de
flags no converge (abreviaturas de prefijo, flags cortos agrupados con valor
pegado, rutas relativas pegadas a un flag, flags forzados anulables, la
etiqueta de escritura divergiendo de la validacion real). Esta suite ya NO
prueba "el flag X esta en la denylist": prueba que un flag que NO figura en
la tabla PERMITIDA de ese binario/subcomando se rechaza siempre, sea cual sea
su forma (nombre completo, abreviado, agrupado con otros, con valor pegado).
Como consecuencia, varios casos que antes "pasaban por no estar en ninguna
denylist" (p. ej. un flag inventado en `ls`) ahora se rechazan por defecto: es
el cambio de fondo que pedia el rediseño, no una regresion.
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
        # Subcomandos que ni siquiera figuran en la tabla de subcomandos
        # permitidos de git (mutan el repo): rechazados por nombre.
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
        # `git -c core.pager=cmd status`: ningun flag puede preceder al
        # subcomando (el propio '-c' tampoco figura en ninguna tabla).
        m = self._rechaza_git(["-c", "core.pager=evil", "status"])
        self.assertIn("antes del subcomando", m)

    def test_git_lectura_permitida(self):
        for args in (["status"], ["log", "--oneline"], ["diff", "--stat"],
                     ["show"], ["branch"], ["remote", "-v"], ["rev-parse", "HEAD"]):
            ok, motivo, _ = _validar(_decision([_cmd("git", args)]))
            self.assertTrue(ok, f"git {args}: {motivo}")

    def test_git_no_pager_inyectado(self):
        ok, _, argv = comandos.validar_forma_comando("git", ["status", "-s"])
        self.assertTrue(ok)
        self.assertEqual(argv, ["git", "--no-pager", "status", "-s"])


class TestDenylistGitSubcomandosDuales(unittest.TestCase):
    """Subcomandos DUALES de git: SIN argumentos posicionales solo listan,
    pero MUTAN el repo de verdad con el argumento adecuado (crear/borrar/
    renombrar tags, ramas o remotos; mover HEAD; purgar el reflog).
    Comprobado en vivo en un repo de usar-y-tirar antes del rediseño (ver
    informe). Bajo la NUEVA whitelist, cada uno de estos subcomandos declara
    un `max_posicionales` en su propia tabla (0 para tag/branch/remote/
    reflog, 1 para symbolic-ref): ningun argumento posicional que supere ese
    limite pasa nunca, sea cual sea su nombre (no hace falta enumerar
    "add"/"remove"/"rename"/"expire"/"delete"/"drop" una por una).
    """

    def _rechaza_git(self, args):
        ok, motivo, _ = _validar(_decision([_cmd("git", args)]))
        self.assertFalse(ok, f"git {args} deberia rechazarse")
        return motivo

    def _permite_git(self, args):
        ok, motivo, _ = _validar(_decision([_cmd("git", args)]))
        self.assertTrue(ok, f"git {args}: {motivo}")

    # -- tag: crea/borra con argumento posicional -----------------------------

    def test_git_tag_crea_rechazado(self):
        m = self._rechaza_git(["tag", "v1.0"])
        self.assertIn("tag", m)

    def test_git_tag_crea_con_commit_rechazado(self):
        self._rechaza_git(["tag", "v1.0", "HEAD"])

    def test_git_tag_borra_rechazado(self):
        # '-d' no figura en la tabla de 'tag' (solo '-l'/'--list'): se
        # rechaza como flag desconocido, sin necesitar detectar "-d" como
        # "de borrado" especificamente.
        self._rechaza_git(["tag", "-d", "v1.0"])

    def test_git_tag_a_secas_permitido(self):
        self._permite_git(["tag"])

    def test_git_tag_listado_con_flag_permitido(self):
        self._permite_git(["tag", "-l"])

    # -- branch: crea/borra/renombra con argumento posicional -----------------

    def test_git_branch_crea_rechazado(self):
        m = self._rechaza_git(["branch", "nueva-rama"])
        self.assertIn("branch", m)

    def test_git_branch_borra_rechazado(self):
        self._rechaza_git(["branch", "-d", "nueva-rama"])

    def test_git_branch_renombra_rechazado(self):
        self._rechaza_git(["branch", "-m", "old", "new"])

    def test_git_branch_set_upstream_to_rechazado(self):
        # Vector de la 2a auditoria: `--set-upstream-to`/`--unset-upstream`/
        # `--edit-description` mutan `.git/config` y NO son argumentos
        # posicionales, asi que el limite de posicionales no bastaba: hace
        # falta que el flag en si mismo no figure en la tabla de 'branch'.
        m = self._rechaza_git(["branch", "--set-upstream-to=origin/main"])
        self.assertIn("branch", m)

    def test_git_branch_unset_upstream_rechazado(self):
        self._rechaza_git(["branch", "--unset-upstream"])

    def test_git_branch_edit_description_rechazado(self):
        self._rechaza_git(["branch", "--edit-description"])

    def test_git_branch_a_secas_permitido(self):
        self._permite_git(["branch"])

    def test_git_branch_flags_listado_permitidos(self):
        for args in (["branch", "-a"], ["branch", "-v"], ["branch", "-r"]):
            self._permite_git(args)

    # -- remote: anade/quita/renombra con argumento posicional ----------------

    def test_git_remote_add_rechazado(self):
        m = self._rechaza_git(["remote", "add", "origin", "https://x/y.git"])
        self.assertIn("remote", m)

    def test_git_remote_remove_rechazado(self):
        self._rechaza_git(["remote", "remove", "origin"])

    def test_git_remote_rename_rechazado(self):
        self._rechaza_git(["remote", "rename", "old", "new"])

    def test_git_remote_a_secas_permitido(self):
        self._permite_git(["remote"])

    def test_git_remote_v_permitido(self):
        # Regresion: `git remote -v` debe seguir funcionando.
        self._permite_git(["remote", "-v"])

    # -- symbolic-ref: dos posicionales, o -d/--delete, mutan ------------------

    def test_git_symbolic_ref_mover_head_rechazado(self):
        m = self._rechaza_git(["symbolic-ref", "HEAD", "refs/heads/otra"])
        self.assertIn("symbolic-ref", m)

    def test_git_symbolic_ref_delete_rechazado(self):
        # '-d'/'--delete' no figuran en la tabla de 'symbolic-ref' (solo
        # '-q'/'--short'): se rechazan como flag desconocido.
        self._rechaza_git(["symbolic-ref", "-d", "HEAD"])
        self._rechaza_git(["symbolic-ref", "--delete", "HEAD"])

    def test_git_symbolic_ref_lectura_permitida(self):
        self._permite_git(["symbolic-ref", "HEAD"])

    # -- reflog: cualquier posicional (sub-accion o ref) muta o se pierde -----

    def test_git_reflog_expire_rechazado(self):
        m = self._rechaza_git(["reflog", "expire", "--expire=now", "--all"])
        self.assertIn("reflog", m)

    def test_git_reflog_delete_rechazado(self):
        self._rechaza_git(["reflog", "delete", "HEAD@{0}"])

    def test_git_reflog_drop_rechazado(self):
        self._rechaza_git(["reflog", "drop", "HEAD@{0}"])

    def test_git_reflog_a_secas_permitido(self):
        self._permite_git(["reflog"])

    def test_git_reflog_con_ref_concreto_ya_no_se_admite(self):
        # Cambio de fondo deliberado del rediseño: al poner
        # `max_posicionales=0` para bloquear POR CONSTRUCCION las
        # sub-acciones de escritura (expire/delete/drop), se pierde tambien
        # `git reflog <ref-concreto>` (p. ej. 'master'). Funcionalidad
        # marginal que el mando acepta perder a cambio de no tener que
        # enumerar sub-acciones de escritura una por una.
        self._rechaza_git(["reflog", "master"])

    # -- defensa en profundidad: la etiqueta de confirmacion tampoco miente --

    def test_comando_puede_escribir_detecta_subcomandos_duales(self):
        # `comando_puede_escribir` es la segunda barrera (etiqueta honesta de
        # confirmacion), DERIVADA de la misma tabla (vuelve a llamar a
        # `validar_forma_comando`). Se comprueba directamente porque en
        # circulacion normal `validar_forma_comando` ya bloquea antes estos
        # argv.
        casos_mutan = [
            ["git", "tag", "v1.0"],
            ["git", "branch", "nueva-rama"],
            ["git", "remote", "add", "origin", "url"],
            ["git", "symbolic-ref", "HEAD", "refs/heads/otra"],
            ["git", "reflog", "expire", "--all"],
            ["git", "branch", "--set-upstream-to=origin/main"],
        ]
        for argv in casos_mutan:
            self.assertTrue(
                comandos.comando_puede_escribir(argv), f"{argv} deberia marcarse",
            )
        casos_leen = [
            ["git", "--no-pager", "tag"],
            ["git", "--no-pager", "branch", "-a"],
            ["git", "--no-pager", "remote", "-v"],
            ["git", "--no-pager", "symbolic-ref", "HEAD"],
            ["git", "--no-pager", "reflog"],
        ]
        for argv in casos_leen:
            self.assertFalse(
                comandos.comando_puede_escribir(argv), f"{argv} NO deberia marcarse",
            )


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

    def test_systemctl_host_machine_prohibidos(self):
        for flag in ("-H", "--host", "-M", "--machine"):
            ok, _, _ = comandos.validar_forma_comando(
                "systemctl", ["status", flag, "x"],
            )
            self.assertFalse(ok, f"systemctl status {flag} deberia rechazarse")

    def test_systemctl_roo_abreviatura_de_root_rechazada(self):
        # `--root` ni siquiera figura en la tabla de systemctl: una
        # abreviatura ('--roo=') tampoco puede alcanzarlo.
        ok, _, _ = comandos.validar_forma_comando(
            "systemctl", ["status", "--roo=/tmp"],
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

    def test_journalctl_no_pager_dedup(self):
        # Igual que --user: si el cerebro ya incluye --no-pager (p. ej. al
        # reproducir literalmente el ejemplo de un vector de auditoria), no
        # debe duplicarse ni rechazarse: --no-pager SI figura en la tabla de
        # journalctl (a diferencia de git, journalctl no exige que preceda a
        # nada: es un flag mas entre otros).
        ok, _, argv = comandos.validar_forma_comando(
            "journalctl", ["--user", "--no-pager", "-n", "5"],
        )
        self.assertTrue(ok, argv)
        self.assertEqual(argv.count("--no-pager"), 1)
        self.assertEqual(argv.count("--user"), 1)

    def test_systemctl_no_pager_explicito_no_se_rechaza(self):
        # A diferencia de git (donde --no-pager es un flag GLOBAL que debe
        # preceder al subcomando y por tanto el cerebro nunca deberia
        # incluirlo), la sintaxis real de systemctl permite `--no-pager`
        # DESPUES del verbo (comprobado en vivo: `systemctl --user status
        # --no-pager` funciona). Por eso --no-pager si figura en la tabla
        # compartida de subcomandos de systemctl.
        ok, _, argv = comandos.validar_forma_comando(
            "systemctl", ["status", "--no-pager", "x"],
        )
        self.assertTrue(ok, argv)
        self.assertEqual(argv.count("--no-pager"), 1)

    def test_journalctl_system_prohibido(self):
        ok, _, _ = comandos.validar_forma_comando("journalctl", ["--system"])
        self.assertFalse(ok)

    def test_journalctl_merge_prohibido(self):
        # `-m`/`--merge` fusiona el journal de TODOS los namespaces
        # (incluido el de sistema): anularia `--user` igual que `--system`.
        for flag in ("-m", "--merge"):
            ok, _, _ = comandos.validar_forma_comando("journalctl", [flag])
            self.assertFalse(ok, f"journalctl {flag} deberia rechazarse")

    def test_journalctl_machine_host_prohibidos(self):
        for flag in ("-M", "--machine", "-H", "--host"):
            ok, _, _ = comandos.validar_forma_comando("journalctl", [flag])
            self.assertFalse(ok, f"journalctl {flag} deberia rechazarse")


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

    def test_find_exec_global_prohibido_aunque_no_este_en_allowlist(self):
        # `find` ni siquiera esta en la allowlist de binarios: se rechaza por
        # el binario, no por el flag. El resto de binarios permitidos
        # tampoco tienen "-exec"/"-delete" en su propia tabla (no aportan
        # valor de lectura y colisionarian con la familia find-exec).
        ok, _, _ = comandos.validar_forma_comando("find", ["-exec", "rm"])
        self.assertFalse(ok)
        ok, _, _ = comandos.validar_forma_comando("ls", ["-exec", "rm"])
        self.assertFalse(ok)
        ok, _, _ = comandos.validar_forma_comando("wc", ["-delete"])
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
        for arg in ("../x", "a/../b", "~/repos/../..", "/home/usuario/.."):
            ok, _, _ = comandos.validar_forma_comando("cat", [arg])
            self.assertFalse(ok, f"'..' en {arg!r} deberia rechazarse")

    def test_dotdot_en_valor_de_flag_con_igual_rechazado(self):
        # Cierra el hueco especifico de `--flag=../x`: el token CRUDO
        # completo ("--valor=../etc") no siempre contiene un segmento '..'
        # exacto tras el '=' (aqui, tras separarlo, "--valor" no es igual a
        # ".."), pero el VALOR ya separado ("../etc") SI se comprueba de
        # forma independiente en la segunda pasada de `validar_forma_
        # comando`. Se usa una tabla sintetica con un flag de valor
        # (ningun binario real de v1 necesita uno cuyo valor sea ruta y
        # ademas admita '..'; el mecanismo se prueba aqui en aislamiento).
        politica_original = dict(comandos.COMANDOS_PERMITIDOS)
        comandos.COMANDOS_PERMITIDOS["_test_dotdot_en_valor"] = comandos.PoliticaComando(
            tabla=comandos.TablaFlags(largos={
                "--valor": comandos._valor(es_ruta=True),
            }),
        )
        try:
            ok, motivo, _ = comandos.validar_forma_comando(
                "_test_dotdot_en_valor", ["--valor=../etc"],
            )
            self.assertFalse(ok)
            self.assertIn("..", motivo)
        finally:
            comandos.COMANDOS_PERMITIDOS.clear()
            comandos.COMANDOS_PERMITIDOS.update(politica_original)

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


# -- 11. Endurecimiento post-auditorias adversariales (rediseño whitelist) -----
#
# Dos auditorias demostraron que la denylist de flags no converge: (1)
# abreviaturas de prefijo de flags largos (`--fil=X` == `--file`); (2) flags
# cortos agrupados con el valor pegado sin separador (`-if/etc/passwd` es
# `-i` + `-f /etc/passwd` para grep); (3) rutas relativas pegadas a un flag
# nunca se confinaban; (4) un flag_forzado (`--user`) se podia anular con uno
# posterior de sentido contrario (`--system`, `-m/--merge`); (5)
# `comando_puede_escribir()` era una lista aparte que divergia de la
# validacion real. Esta seccion cubre la CLASE de cada fallo con la
# correccion estructural (whitelist de flags con normalizacion previa), no
# solo el caso puntual que encontraron las auditorias, mas regresion de los
# usos legitimos (symlinks internos, patrones de busqueda que se llaman como
# un fichero sensible, flags cortos con valor no-ruta).


class TestVuln1AbreviaturaDeFlagLargo(unittest.TestCase):
    """`getopt_long` acepta cualquier ABREVIATURA unica de un flag largo. La
    correccion: la abreviatura se expande SOLO contra los flags largos YA
    permitidos de ese binario/subcomando (`_expandir_largo`); si el flag
    completo no esta en la tabla, ninguna abreviatura del mismo lo alcanza."""

    def _rechaza(self, binario, args):
        ok, motivo, _ = comandos.validar_forma_comando(binario, args)
        self.assertFalse(ok, f"{binario} {args} deberia rechazarse")
        return motivo

    def test_journalctl_abreviatura_de_file_rechazada(self):
        self._rechaza("journalctl", ["--fil=/var/log/journal/x/system.journal"])

    def test_journalctl_abreviatura_de_root_rechazada(self):
        self._rechaza("journalctl", ["--roo=/tmp"])

    def test_du_abreviatura_de_files0_from_rechazada(self):
        self._rechaza("du", ["--files0-fro=lista"])

    def test_git_abreviatura_de_output_rechazada(self):
        self._rechaza("git", ["log", "--out=/tmp/x"])

    def test_abreviatura_de_flag_si_permitido_se_resuelve(self):
        # Regresion: una abreviatura de un flag SI permitido debe resolverse
        # a su forma canonica (no se rechaza solo por venir abreviado).
        ok, _, argv = comandos.validar_forma_comando("git", ["log", "--onel"])
        self.assertTrue(ok)
        self.assertIn("--oneline", argv)

    def test_abreviatura_ambigua_rechazada(self):
        # 'du' tiene '--max-depth'; un prefijo que coincidiera con mas de un
        # flag largo permitido debe rechazarse por ambiguo. Se fabrica el
        # caso con una tabla sintetica para no depender de que 'du' tenga
        # hoy dos flags con el mismo prefijo.
        tabla = comandos.TablaFlags(largos={
            "--max-depth": comandos._valor(), "--max-count": comandos._valor(),
        })
        analisis, motivo = comandos._analizar_flags("test", ["--max=5"], tabla)
        self.assertIsNone(analisis)
        self.assertIn("ambigua", motivo)


class TestVuln2FlagCortoAgrupadoConValorPegado(unittest.TestCase):
    """Un flag corto DESCONOCIDO no se puede colar agrupandolo con uno
    permitido y pegando su "valor" sin separador (`-if/etc/passwd` para
    grep: bajo la denylist vieja, solo se miraban los 2 primeros caracteres
    del token, `-i`, y el resto pasaba desapercibido)."""

    def _rechaza(self, binario, args):
        ok, motivo, _ = comandos.validar_forma_comando(binario, args)
        self.assertFalse(ok, f"{binario} {args} deberia rechazarse")
        return motivo

    def test_grep_if_etc_passwd_rechazado(self):
        m = self._rechaza("grep", ["-if/etc/passwd", "."])
        self.assertIn("-f", m)

    def test_rg_if_etc_passwd_rechazado(self):
        self._rechaza("rg", ["-if/etc/passwd"])

    def test_tail_qf_rechazado(self):
        # `-qf fichero`: agrupado, con '-f' (follow, no permitido) despues de
        # '-q'. Antes pasaba entero porque el primer caracter tras '-' (q)
        # no estaba en ninguna denylist de 2 caracteres.
        self._rechaza("tail", ["-qf", "fichero"])

    def test_valor_pegado_a_flag_de_valor_no_se_confunde(self):
        # Regresion: un flag corto que SI lleva valor puede llevarlo pegado
        # sin que se interprete como otro flag mas (`-A5` de grep: '5' es el
        # valor de '-A', no un flag).
        ok, _, argv = comandos.validar_forma_comando("grep", ["-A5", "patron"])
        self.assertTrue(ok)
        self.assertIn("-A", argv)
        self.assertIn("5", argv)

    def test_bundling_de_solo_booleanos_sigue_funcionando(self):
        # Regresion: `ls -la` (dos flags booleanos agrupados) debe seguir
        # funcionando igual que antes.
        ok, _, argv = comandos.validar_forma_comando("ls", ["-la"])
        self.assertTrue(ok)
        self.assertEqual(argv, ["ls", "-l", "-a"])


class TestVuln3RutaRelativaPegadaAFlag(unittest.TestCase):
    """Una ruta pegada al valor de un flag se confina EXACTAMENTE igual que
    un argumento posicional, sea absoluta, de HOME, o RELATIVA (la relativa
    es la que antes nunca se comprobaba: solo se miraban rutas explicitas
    '/'/'~')."""

    def setUp(self):
        # Tabla sintetica con un flag que SI lleva una ruta como valor, para
        # probar el mecanismo de forma aislada (ningun binario de v1 necesita
        # un flag asi: minimo privilegio deja el "valor de flag = ruta"
        # dormido, pero implementado y testeado).
        self._politica_original = dict(comandos.COMANDOS_PERMITIDOS)
        comandos.COMANDOS_PERMITIDOS["_test_ruta_en_flag"] = comandos.PoliticaComando(
            tabla=comandos.TablaFlags(largos={
                "--desde": comandos._valor(es_ruta=True),
            }),
        )
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        comandos.COMANDOS_PERMITIDOS.clear()
        comandos.COMANDOS_PERMITIDOS.update(self._politica_original)

    def test_valor_pegado_con_igual_fuera_de_raices_bloqueado(self):
        norm = _norm(_decision([
            _cmd("_test_ruta_en_flag", ["--desde=/etc/passwd"]),
        ]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertFalse(v.permitido)
        self.assertIn("/etc/passwd", v.motivo_bloqueo)

    def test_valor_pegado_ruta_sensible_bloqueado(self):
        norm = _norm(_decision([
            _cmd("_test_ruta_en_flag", ["--desde=~/.ssh/id_rsa"]),
        ]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertFalse(v.permitido)

    def test_valor_pegado_dentro_de_raices_permitido(self):
        dentro = str(_raiz() / "HANDOFF.md")
        norm = _norm(_decision([
            _cmd("_test_ruta_en_flag", [f"--desde={dentro}"]),
        ]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_valor_relativo_de_flag_tambien_se_confina(self):
        # Este es el hueco especifico que la 2a auditoria demostro: una
        # ruta RELATIVA pegada a un flag ('--desde=ruta_relativa') nunca se
        # confinaba. Aqui se comprueba que SI pasa por el mismo
        # confinamiento que un posicional relativo (symlink-escape).
        with tempfile.TemporaryDirectory() as tmp_fuera:
            secreto = Path(tmp_fuera) / "secreto.txt"
            secreto.write_text("fuera\n")
            enlace = _raiz() / "_test_enlace_valor_relativo_pi2.txt"
            enlace.symlink_to(secreto)
            self.addCleanup(lambda: enlace.unlink(missing_ok=True))

            norm = _norm(_decision([
                _cmd("_test_ruta_en_flag", [f"--desde={enlace.name}"]),
            ]))
            v = seguridad.evaluar_comandos(norm, cwd=_raiz())
            self.assertFalse(v.permitido)
            self.assertIn("enlace simbolico", v.motivo_bloqueo)

    def test_valor_de_flag_sin_es_ruta_no_se_confina_de_mas(self):
        # Regresion: un flag de VALOR que NO esta marcado `es_ruta=True`
        # (p. ej. -A5 de grep, o -n de journalctl) no debe pasar por el
        # confinamiento aunque su valor sea un numero cualquiera.
        ok, _, _ = comandos.validar_forma_comando("journalctl", ["-n", "20"])
        self.assertTrue(ok)


class TestVuln4FlagsForzadosNoAnulables(unittest.TestCase):
    """`journalctl --user` (o `systemctl --user`) se podia anular con un
    flag posterior de sentido contrario (`--system`, `-m/--merge`,
    `-M/--machine`, `-H/--host`). La correccion: esos flags sencillamente no
    figuran en NINGUNA tabla permitida (no hay forma de alcanzarlos, ni
    exacto ni abreviado), y ademas hay una comprobacion EXPLICITA e
    independiente (`flags_incompatibles_forzados`) por si algun dia se
    colara uno por error en la tabla."""

    def test_journalctl_system_no_anula_user(self):
        ok, _, _ = comandos.validar_forma_comando(
            "journalctl", ["--user", "--system", "-n", "5"],
        )
        self.assertFalse(ok)

    def test_journalctl_merge_no_anula_user(self):
        ok, _, _ = comandos.validar_forma_comando(
            "journalctl", ["--user", "-m", "-n", "5"],
        )
        self.assertFalse(ok)

    def test_systemctl_system_no_anula_user(self):
        ok, _, _ = comandos.validar_forma_comando(
            "systemctl", ["status", "--system"],
        )
        self.assertFalse(ok)

    def test_flags_incompatibles_forzados_es_capa_independiente(self):
        # La comprobacion de `flags_incompatibles_forzados` es independiente
        # de que el flag este o no en la tabla: si por error se colara
        # "--system" en la tabla de journalctl, esta capa seguiria
        # bloqueandolo. Se prueba fabricando esa situacion accidental con
        # una tabla/politica sinteticas.
        politica_original = dict(comandos.COMANDOS_PERMITIDOS)
        tabla_con_error = comandos.TablaFlags(largos={"--system": comandos._BOOL})
        comandos.COMANDOS_PERMITIDOS["_test_forzado_incompatible"] = (
            comandos.PoliticaComando(
                tabla=tabla_con_error,
                flags_forzados=("--user",),
                flags_incompatibles_forzados=frozenset({"--system"}),
            )
        )
        try:
            ok, motivo, _ = comandos.validar_forma_comando(
                "_test_forzado_incompatible", ["--system"],
            )
            self.assertFalse(ok)
            self.assertIn("incompatible", motivo)
        finally:
            comandos.COMANDOS_PERMITIDOS.clear()
            comandos.COMANDOS_PERMITIDOS.update(politica_original)


class TestVuln5EtiquetaDeConfirmacionDerivadaDeLaTabla(unittest.TestCase):
    """El texto de confirmacion no debe afirmar 'solo lectura' salvo que se
    haya verificado que lo es; `comando_puede_escribir` debe leer la MISMA
    tabla que `validar_forma_comando` (nunca una lista aparte)."""

    def test_comando_puede_escribir_detecta_marcadores_conocidos(self):
        self.assertTrue(comandos.comando_puede_escribir(
            ["git", "--no-pager", "log", "--output=/tmp/x"]))
        self.assertTrue(comandos.comando_puede_escribir(
            ["journalctl", "--user", "--no-pager", "--file=/tmp/x"]))
        self.assertTrue(comandos.comando_puede_escribir(
            ["tree", "-o/tmp/x"]))

    def test_comando_puede_escribir_false_en_lectura_real(self):
        self.assertFalse(comandos.comando_puede_escribir(
            ["git", "--no-pager", "status"]))
        self.assertFalse(comandos.comando_puede_escribir(["whoami"]))
        self.assertFalse(comandos.comando_puede_escribir(
            ["grep", "-A5", "patron", "archivo.py"]))

    def test_lote_realmente_de_solo_lectura_lo_afirma(self):
        norm = _norm(_decision([_cmd("git", ["status"]), _cmd("whoami")]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido)
        self.assertIn("solo lectura", v.texto_confirmacion.lower())
        self.assertNotIn("posible escritura", v.texto_confirmacion.lower())

    def test_flag_desconocido_en_tabla_marca_escritura_por_fallo_seguro(self):
        # Si `argv` trae un flag que no figura en la tabla del binario (algo
        # que en circulacion normal ya habria bloqueado
        # `validar_forma_comando` antes), la etiqueta NUNCA debe afirmar
        # "solo lectura": fallo seguro.
        self.assertTrue(comandos.comando_puede_escribir(
            ["git", "--no-pager", "log", "--output=/tmp/x"]))

    def test_binario_desconocido_marca_escritura_por_fallo_seguro(self):
        self.assertTrue(comandos.comando_puede_escribir(["bash", "-c", "x"]))


class TestAdemasMaxArgsSobreArgvFinal(unittest.TestCase):
    """`MAX_ARGS_POR_COMANDO` se comprueba sobre el argv FINAL (tras
    normalizar flags e inyectar `flags_forzados`), no solo sobre lo que
    propuso el cerebro."""

    def test_journalctl_con_flags_forzados_cuenta_en_el_total(self):
        # journalctl fuerza --user y --no-pager (2 flags). '-k' es un flag
        # booleano real de journalctl (--dmesg): cada repeticion es un solo
        # token en el argv final, igual que en el argv de entrada.
        n = comandos.MAX_ARGS_POR_COMANDO - 1
        args = ["-k"] * n
        ok, motivo, argv = comandos.validar_forma_comando("journalctl", args)
        self.assertFalse(ok, f"deberia rechazarse: argv tendria "
                              f"{n + 2} tokens tras inyectar --user/--no-pager")
        self.assertIn("demasiados argumentos", motivo)

    def test_justo_en_el_limite_final_se_acepta(self):
        n = comandos.MAX_ARGS_POR_COMANDO - 2  # + 2 forzados = MAX exacto
        args = ["-k"] * n
        ok, motivo, argv = comandos.validar_forma_comando("journalctl", args)
        self.assertTrue(ok, motivo)
        self.assertEqual(len(argv) - 1, comandos.MAX_ARGS_POR_COMANDO)

    def test_sin_flags_forzados_limite_no_cambia(self):
        # No regresion: un binario sin flags_forzados (ls) se sigue
        # comportando igual que antes.
        ok, _, _ = comandos.validar_forma_comando(
            "ls", ["-l"] * comandos.MAX_ARGS_POR_COMANDO)
        self.assertTrue(ok)
        ok, _, _ = comandos.validar_forma_comando(
            "ls", ["-l"] * (comandos.MAX_ARGS_POR_COMANDO + 1))
        self.assertFalse(ok)


class TestVuln0EscapePorSymlinkYFalsosPositivos(unittest.TestCase):
    """No regresiones del endurecimiento previo (symlinks, heuristica de
    nombres sensibles) al pasar de denylist a whitelist de flags: un
    symlink DENTRO de una raiz autorizada que apunte FUERA no debe pasar el
    confinamiento; uno INTERNO (destino tambien autorizado) debe seguir
    funcionando; y un argumento que simplemente se LLAME como un fichero
    sensible (sin serlo) no debe rechazarse."""

    def setUp(self):
        self._tmp_fuera = tempfile.TemporaryDirectory()
        self.fuera = Path(self._tmp_fuera.name)
        self.addCleanup(self._tmp_fuera.cleanup)
        self.secreto_fuera = self.fuera / "secreto.txt"
        self.secreto_fuera.write_text("SECRETO_FUERA_DE_LA_RAIZ\n")

        self.enlace = _raiz() / "_test_enlace_symlink_pi2.txt"
        self.enlace.symlink_to(self.secreto_fuera)
        self.addCleanup(self._quitar, self.enlace)

        self.enlace_interno = _raiz() / "_test_enlace_interno_pi2.txt"
        self.enlace_interno.symlink_to(_raiz() / "HANDOFF.md")
        self.addCleanup(self._quitar, self.enlace_interno)

    @staticmethod
    def _quitar(ruta: Path):
        try:
            ruta.unlink()
        except OSError:
            pass

    def test_symlink_token_relativo_bloqueado(self):
        norm = _norm(_decision([_cmd("cat", [self.enlace.name])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertFalse(v.permitido)
        self.assertIn("symlink", v.motivo_bloqueo)

    def test_symlink_token_absoluto_bloqueado(self):
        norm = _norm(_decision([_cmd("cat", [str(self.enlace)])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertFalse(v.permitido)
        self.assertIn("symlink", v.motivo_bloqueo)

    def test_extremo_a_extremo_symlink_no_lee_el_secreto(self):
        norm = _norm(_decision([_cmd("cat", [self.enlace.name])]))
        with patch("arquitecto.ejecutor.subprocess.run") as m:
            lote = ejecutor.ejecutar_comandos(
                norm, confirmador=lambda _t: True, cwd=_raiz(),
            )
        m.assert_not_called()
        self.assertTrue(lote.bloqueado)

    def test_symlink_interno_no_se_rompe(self):
        norm = _norm(_decision([_cmd("cat", [self.enlace_interno.name])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)

    def test_patron_con_nombre_sensible_no_se_bloquea_por_error(self):
        norm = _norm(_decision([_cmd("grep", ["id_rsa", "."])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)


class TestFalsosPositivosEtiquetaEscritura(unittest.TestCase):
    """La 2a auditoria tambien demostro falsos POSITIVOS de la etiqueta
    `[POSIBLE ESCRITURA]` en comandos realmente de solo lectura (`grep -i
    patron .`, `ls -o`, `ls -f`, `df -i`): bajo la whitelist, estos flags
    son simplemente flags booleanos permitidos como cualquier otro, sin
    ningun marcador especial que los confunda con escritura."""

    def test_grep_i_no_se_marca_como_escritura(self):
        norm = _norm(_decision([_cmd("grep", ["-i", "patron", "."])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)
        self.assertNotIn("posible escritura", v.texto_confirmacion.lower())

    def test_ls_o_no_se_marca_como_escritura(self):
        norm = _norm(_decision([_cmd("ls", ["-o"])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)
        self.assertNotIn("posible escritura", v.texto_confirmacion.lower())

    def test_ls_f_no_se_marca_como_escritura(self):
        norm = _norm(_decision([_cmd("ls", ["-F"])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)
        self.assertNotIn("posible escritura", v.texto_confirmacion.lower())

    def test_df_i_no_se_marca_como_escritura(self):
        norm = _norm(_decision([_cmd("df", ["-i"])]))
        v = seguridad.evaluar_comandos(norm, cwd=_raiz())
        self.assertTrue(v.permitido, v.motivo_bloqueo)
        self.assertNotIn("posible escritura", v.texto_confirmacion.lower())


# -- 12. Tercera auditoria: H-1 (dedup de forzados por flags_vistos) ----------


class TestH1FlagForzadoNoAnulablePorValorLiteral(unittest.TestCase):
    """Tercera auditoria (H-1, BLOQUEANTE): `analisis.tokens` es la secuencia
    PLANA de flags, valores y posicionales. Bastaba con que el literal
    '--user' apareciera como VALOR de cualquier flag `_valor()` (o como
    posicional tras `--`) para que la deduplicacion de `flags_forzados`
    (linea 949 original) creyera que el flag ya estaba puesto y dejara de
    inyectarlo. Demostrado en vivo con:
        journalctl --grep --user --grep=. -n 3   (el primer --grep solo
            existe para "consumir" el literal --user como su valor)
        systemctl cat sshd -- --user
        systemctl status -- --user
    Correccion: el dedup compara contra `flags_vistos` (flags REALMENTE
    emitidos en posicion de flag), nunca contra `tokens`. Estos tests cubren
    el literal '--user' como valor de CADA flag `_valor()` de journalctl (16)
    y de systemctl, y como posicional tras `--`, mas el caso general (mismo
    mecanismo, otro flag forzado: `--no-pager` de git)."""

    def _valores_de(self, tabla) -> list[str]:
        """Nombres canonicos de todos los flags `_valor()` (cortos y largos)
        de una `TablaFlags`."""
        nombres = []
        for nombre, spec in {**tabla.cortos, **tabla.largos}.items():
            if spec.lleva_valor:
                nombres.append(nombre)
        return nombres

    def test_journalctl_user_como_valor_de_cada_flag_de_valor(self):
        flags_de_valor = self._valores_de(comandos._JOURNALCTL_TABLA)
        self.assertEqual(len(flags_de_valor), 16, flags_de_valor)
        for flag in flags_de_valor:
            with self.subTest(flag=flag):
                ok, motivo, argv = comandos.validar_forma_comando(
                    "journalctl", [flag, "--user"],
                )
                self.assertTrue(ok, motivo)
                self.assertIn(
                    "--user", argv,
                    f"--user ausente del argv final tras {flag}=--user: {argv}",
                )

    def test_journalctl_user_reproduccion_exacta_del_vector_auditado(self):
        # journalctl --grep --user --grep=. -n 3 : el primer --grep consume
        # el literal '--user' como su VALOR; el segundo --grep gana en
        # getopt. Antes del fix, '--user' figuraba en la secuencia plana de
        # tokens (como valor) y el Arquitecto NO lo inyectaba: el comando se
        # ejecutaba en ambito SISTEMA.
        ok, motivo, argv = comandos.validar_forma_comando(
            "journalctl", ["--grep", "--user", "--grep=.", "-n", "3"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("--user", argv)
        # El '--user' FORZADO va antepuesto, antes de cualquier '--grep'.
        self.assertEqual(argv[0], "journalctl")
        self.assertIn("--user", argv[:3])

    def test_journalctl_user_como_posicional_tras_separador(self):
        # '--user' aqui es un dato posicional (tras '--'), no el flag: el
        # Arquitecto sigue inyectando SU '--user' de ambito ademas del dato
        # literal, asi que aparece dos veces en el argv final (una como
        # flag forzado, otra como el posicional que propuso el cerebro).
        ok, motivo, argv = comandos.validar_forma_comando(
            "journalctl", ["-n", "3", "--", "--user"],
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(argv.count("--user"), 2)
        self.assertEqual(argv[1], "--user")  # el FORZADO va antepuesto

    def test_systemctl_user_como_valor_de_cada_flag_de_valor(self):
        flags_de_valor = self._valores_de(comandos._SYSTEMCTL_TABLA_COMPARTIDA)
        self.assertTrue(flags_de_valor)
        for flag in flags_de_valor:
            with self.subTest(flag=flag):
                ok, motivo, argv = comandos.validar_forma_comando(
                    "systemctl", ["status", flag, "--user"],
                )
                self.assertTrue(ok, motivo)
                self.assertIn("--user", argv)

    def test_systemctl_cat_sshd_user_como_posicional_vector_auditado(self):
        # `systemctl cat sshd -- --user` (vector demostrado en la auditoria).
        ok, motivo, argv = comandos.validar_forma_comando(
            "systemctl", ["cat", "sshd", "--", "--user"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("--user", argv)

    def test_systemctl_status_user_como_posicional_vector_auditado(self):
        # `systemctl status -- --user` (vector demostrado en la auditoria).
        ok, motivo, argv = comandos.validar_forma_comando(
            "systemctl", ["status", "--", "--user"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("--user", argv)

    def test_systemctl_list_unit_files_user_como_posicional(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "systemctl", ["list-unit-files", "--", "--user"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("--user", argv)

    def test_mecanismo_general_no_pager_de_git_no_se_anula_por_valor(self):
        # Mismo mecanismo, otro binario/flag forzado: si '--no-pager' se cuela
        # como VALOR de --author, el forzado de git debe seguir inyectandose.
        ok, motivo, argv = comandos.validar_forma_comando(
            "git", ["log", "--author=--no-pager"],
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(argv.count("--no-pager"), 2)  # el forzado + el valor
        self.assertEqual(argv[1], "--no-pager")


# -- 13. Tercera auditoria: H-2 (7 regresiones funcionales) -------------------


class TestH2RegresionesFuncionales(unittest.TestCase):
    """Tercera auditoria (H-2, BLOQUEANTE): 7 usos habituales que SI
    funcionaban antes del rediseño de whitelist dejaron de validar. Cada test
    reproduce el uso exacto reportado y comprueba que vuelve a validar."""

    def test_head_num_pegado(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "head", ["-20", "fichero"],
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(argv, ["head", "-20", "fichero"])

    def test_tail_num_pegado(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "tail", ["-50", "fichero"],
        )
        self.assertTrue(ok, motivo)
        self.assertEqual(argv, ["tail", "-50", "fichero"])

    def test_git_log_num_pegado(self):
        ok, motivo, argv = comandos.validar_forma_comando("git", ["log", "-5"])
        self.assertTrue(ok, motivo)
        self.assertIn("-5", argv)

    def test_journalctl_boot_offset_negativo(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "journalctl", ["-b", "-1"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("-b", argv)
        self.assertIn("-1", argv)

    def test_ps_forma_bsd_sin_guion(self):
        ok, motivo, argv = comandos.validar_forma_comando("ps", ["aux"])
        self.assertTrue(ok, motivo)
        self.assertEqual(argv, ["ps", "aux"])

    def test_git_status_porcelain(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "git", ["status", "--porcelain"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("--porcelain", argv)

    def test_systemctl_show_property(self):
        ok, motivo, argv = comandos.validar_forma_comando(
            "systemctl", ["show", "-p", "Description", "sshd.service"],
        )
        self.assertTrue(ok, motivo)
        self.assertIn("-p", argv)
        self.assertIn("Description", argv)

    def test_numero_pegado_no_se_activa_en_binario_sin_marcador(self):
        # `acepta_numero_corto` es por tabla: un binario que NO lo declara
        # (p. ej. `ls`) sigue desagrupando '-20' digito a digito y
        # rechazando (ninguna tabla de ls tiene flags '-2'/'-0').
        ok, _, _ = comandos.validar_forma_comando("ls", ["-20"])
        self.assertFalse(ok)


# -- 14. Tercera auditoria: H-3 (git help lanza man/navegador) ----------------


class TestH3GitHelpExcluido(unittest.TestCase):
    """Tercera auditoria (H-3, MEDIA): `git help <topic>` lanza `man` (o un
    navegador via `help.format=web`/`web.browser` del repo), un subproceso
    AJENO a la allowlist. Verificado en vivo: `git help git` renderiza la
    man page. `help` se retiro de `_GIT_SUBCOMANDOS`."""

    def test_git_help_rechazado(self):
        ok, motivo, _ = comandos.validar_forma_comando("git", ["help", "git"])
        self.assertFalse(ok)
        self.assertIn("no permitido", motivo)

    def test_git_help_a_secas_rechazado(self):
        ok, _, _ = comandos.validar_forma_comando("git", ["help"])
        self.assertFalse(ok)

    def test_help_no_figura_en_subcomandos_permitidos(self):
        self.assertNotIn("help", comandos._GIT_SUBCOMANDOS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
