"""
Tests de la Fase 3 del rediseno del Arquitecto del Castillo.

Cubre:
    - arquitecto.seguridad: veredicto de gating (bloqueos, avisos,
      confirmacion, rutas protegidas, metacaracteres, dependencias).
    - arquitecto.ejecutor: ejecucion via subprocess (mockeado), dry-run,
      confirmacion por callback, timeout, conectividad, y `componer` con
      parar_si_falla.
    - arquitecto.trazas: escritura/lectura JSONL tolerante.

El subprocess se MOCKEA en todos los tests del ejecutor: la suite no debe
depender de que los wrappers reales esten instalados ni tocar el sistema.
Las trazas se escriben en ficheros temporales aislados.
"""

from __future__ import annotations

import subprocess
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

from arquitecto import ejecutor, seguridad, trazas  # noqa: E402
from arquitecto.registro import (  # noqa: E402
    Argumento,
    ContextoLlm,
    Dependencias,
    Manifiesto,
    Operacion,
    Seguridad,
)
from arquitecto.validador import validar_decision  # noqa: E402


# -- Fabricas de manifiestos sinteticos ----------------------------------------


def _ctx() -> ContextoLlm:
    return ContextoLlm(
        cuando_usar="cuando",
        cuando_no_usar="nunca",
        ejemplos_peticion=("a", "b"),
        palabras_clave=("x",),
    )


def _manifiesto(
    *,
    clave: str = "eco_test",
    comando_base: str = "echo",
    operaciones: tuple[Operacion, ...],
    argumentos: tuple[Argumento, ...] = (),
    requiere_red: bool = False,
    requiere_sudo: bool = False,
    paths_protegidos: tuple[str, ...] = (),
    binarios: tuple[str, ...] = (),
    ficheros_config: tuple[str, ...] = (),
    servicios_systemd: tuple[str, ...] = (),
    tiempo_max: int = 10,
) -> Manifiesto:
    return Manifiesto(
        clave=clave,
        nombre_visible=clave.title(),
        descripcion_corta="manifiesto de prueba",
        categoria="otra",
        version_manifiesto="1.0.0",
        comando_base=comando_base,
        tipo_invocacion="comando_sistema",
        usa_subcomandos=False,
        subcomando_por_defecto=None,
        operaciones=operaciones,
        argumentos=argumentos,
        seguridad=Seguridad(
            permite_argumentos_libres=False,
            requiere_red=requiere_red,
            requiere_sudo=requiere_sudo,
            tiempo_max_segundos=tiempo_max,
            paths_protegidos=paths_protegidos,
        ),
        dependencias=Dependencias(
            binarios=binarios,
            paquetes_python=(),
            ficheros_config=ficheros_config,
            servicios_systemd=servicios_systemd,
        ),
        contexto_llm=_ctx(),
        ruta_fichero=Path("/tmp/manifiesto_falso.toml"),
    )


def _op_lectura(nombre: str = "saludar", flags: tuple[str, ...] = ("hola",),
                args: tuple[str, ...] = ()) -> Operacion:
    return Operacion(
        nombre=nombre,
        descripcion="operacion de lectura",
        flags=flags,
        argumentos_aceptados=args,
        requiere_confirmacion=False,
        peligrosidad="lectura",
        bloquea_terminal=False,
        salida_esperada="texto_corto",
    )


def _op_escritura(nombre: str = "borrar", args: tuple[str, ...] = ()) -> Operacion:
    return Operacion(
        nombre=nombre,
        descripcion="operacion que escribe",
        flags=(),
        argumentos_aceptados=args,
        requiere_confirmacion=True,
        peligrosidad="escritura_local",
        bloquea_terminal=False,
        salida_esperada="texto_corto",
    )


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Sustituto de subprocess.CompletedProcess para los mocks."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# -- Tests de seguridad --------------------------------------------------------


class TestSeguridad(unittest.TestCase):

    def test_lectura_permitida_sin_confirmacion(self):
        man = _manifiesto(operaciones=(_op_lectura(),))
        inv = {
            "clave_automatizacion": "eco_test",
            "nombre_operacion": "saludar",
            "argumentos": {},
            "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False,
            "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertTrue(v.permitido)
        self.assertFalse(v.requiere_confirmacion)
        self.assertIsNone(v.motivo_bloqueo)

    def test_escritura_exige_confirmacion(self):
        man = _manifiesto(operaciones=(_op_escritura(),))
        inv = {
            "clave_automatizacion": "eco_test",
            "nombre_operacion": "borrar",
            "argumentos": {},
            "peligrosidad_efectiva": "escritura_local",
            "requiere_confirmacion": True,
            "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)

    def test_bloquea_terminal_se_rechaza(self):
        op = Operacion(
            nombre="seguir", descripcion="tail -f", flags=(),
            argumentos_aceptados=(), requiere_confirmacion=False,
            peligrosidad="lectura", bloquea_terminal=True,
            salida_esperada="interactivo",
        )
        man = _manifiesto(operaciones=(op,))
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "seguir",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": True,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertFalse(v.permitido)
        self.assertIn("bloquea", v.motivo_bloqueo)

    def test_sudo_se_rechaza(self):
        man = _manifiesto(operaciones=(_op_lectura(),), requiere_sudo=True)
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertFalse(v.permitido)
        self.assertIn("sudo", v.motivo_bloqueo)

    def test_metacaracter_shell_en_argumento_se_rechaza(self):
        arg = Argumento(
            clave="texto", descripcion="t", tipo="cadena", obligatorio=False,
            forma_paso="posicional",
        )
        op = _op_lectura(args=("texto",))
        man = _manifiesto(operaciones=(op,), argumentos=(arg,))
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {"texto": "hola; rm -rf /"},
            "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertFalse(v.permitido)
        self.assertIn("metacaracter", v.motivo_bloqueo)

    def test_ruta_protegida_se_rechaza(self):
        arg = Argumento(
            clave="ruta", descripcion="r", tipo="ruta_fichero",
            obligatorio=False, forma_paso="posicional",
        )
        op = _op_escritura(args=("ruta",))
        man = _manifiesto(
            operaciones=(op,), argumentos=(arg,),
            paths_protegidos=("/home/sun/protegido",),
        )
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "borrar",
            "argumentos": {"ruta": "/home/sun/protegido/datos.db"},
            "peligrosidad_efectiva": "escritura_local",
            "requiere_confirmacion": True, "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertFalse(v.permitido)
        self.assertIn("protegida", v.motivo_bloqueo)

    def test_binario_faltante_se_rechaza(self):
        man = _manifiesto(
            operaciones=(_op_lectura(),),
            binarios=("binario_que_no_existe_xyz123",),
        )
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }
        v = seguridad.evaluar_invocacion(inv, man)
        self.assertFalse(v.permitido)
        self.assertIn("binarios", v.motivo_bloqueo)

    def test_servicio_inactivo_solo_avisa(self):
        man = _manifiesto(
            operaciones=(_op_lectura(),),
            servicios_systemd=("servicio_inexistente_xyz.service",),
        )
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }
        with patch.object(seguridad, "_servicio_activo", return_value=False):
            v = seguridad.evaluar_invocacion(inv, man)
        self.assertTrue(v.permitido)
        self.assertTrue(any("no esta activo" in a for a in v.avisos))

    def _inv_lectura(self) -> dict:
        return {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }

    def test_config_directorio_existente_se_permite(self):
        # Regresion: un directorio declarado en ficheros_config es una
        # dependencia legitima (p.ej. invocador_entorno/modos). El validador
        # usaba is_file() y lo bloqueaba como falso positivo.
        with tempfile.TemporaryDirectory() as tmp:
            man = _manifiesto(
                operaciones=(_op_lectura(),),
                ficheros_config=(tmp,),
            )
            v = seguridad.evaluar_invocacion(self._inv_lectura(), man)
        self.assertTrue(v.permitido)

    def test_config_fichero_existente_se_permite(self):
        with tempfile.NamedTemporaryFile() as tmp:
            man = _manifiesto(
                operaciones=(_op_lectura(),),
                ficheros_config=(tmp.name,),
            )
            v = seguridad.evaluar_invocacion(self._inv_lectura(), man)
        self.assertTrue(v.permitido)

    def test_config_inexistente_se_rechaza(self):
        # La garantia de seguridad se mantiene: una ruta que no existe (o un
        # symlink roto, que exists() resuelve a False) sigue bloqueando.
        man = _manifiesto(
            operaciones=(_op_lectura(),),
            ficheros_config=("/ruta/que/no/existe/xyz123",),
        )
        v = seguridad.evaluar_invocacion(self._inv_lectura(), man)
        self.assertFalse(v.permitido)
        self.assertIn("configuracion", v.motivo_bloqueo)

    def _op(self, peligrosidad: str, conf: bool, nombre: str = "op") -> Operacion:
        return Operacion(
            nombre=nombre, descripcion="op de prueba", flags=(),
            argumentos_aceptados=(), requiere_confirmacion=conf,
            peligrosidad=peligrosidad, bloquea_terminal=False,
            salida_esperada="texto_corto",
        )

    def _evaluar(self, op: Operacion):
        man = _manifiesto(operaciones=(op,))
        inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": op.nombre,
            "argumentos": {}, "peligrosidad_efectiva": op.peligrosidad,
            "requiere_confirmacion": op.requiere_confirmacion,
            "bloquea_terminal": op.bloquea_terminal,
        }
        return seguridad.evaluar_invocacion(inv, man)

    def test_red_saliente_sin_conf_no_pregunta(self):
        # El caso del cazador_medios: descarga de red con conf=false NO pregunta.
        v = self._evaluar(self._op("red_saliente", conf=False))
        self.assertTrue(v.permitido)
        self.assertFalse(v.requiere_confirmacion)

    def test_escritura_local_sin_conf_no_pregunta(self):
        # Se honra el manifiesto: escritura local con conf=false no pregunta.
        v = self._evaluar(self._op("escritura_local", conf=False))
        self.assertTrue(v.permitido)
        self.assertFalse(v.requiere_confirmacion)

    def test_manifiesto_pide_conf_se_honra(self):
        v = self._evaluar(self._op("escritura_sistema", conf=True))
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)

    def test_destructiva_confirma_aunque_manifiesto_diga_false(self):
        # Suelo duro: 'destructiva' SIEMPRE confirma, ignore el manifiesto.
        v = self._evaluar(self._op("destructiva", conf=False))
        self.assertTrue(v.permitido)
        self.assertTrue(v.requiere_confirmacion)


# -- Tests del ejecutor --------------------------------------------------------


class TestEjecutor(unittest.TestCase):

    def setUp(self):
        self.man = _manifiesto(operaciones=(_op_lectura(),))
        self.inv = {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
        }

    def test_ejecucion_exitosa(self):
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "hola\n", "")) as mock_run:
            res = ejecutor.ejecutar_invocacion(self.inv, self.man)
        mock_run.assert_called_once()
        # shell=False siempre.
        self.assertFalse(mock_run.call_args.kwargs.get("shell", False))
        self.assertTrue(res.exito)
        self.assertEqual(res.codigo_salida, 0)
        self.assertEqual(res.stdout, "hola\n")
        self.assertEqual(res.comando, ("echo", "hola"))

    def test_ejecucion_codigo_no_cero(self):
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(2, "", "boom")):
            res = ejecutor.ejecutar_invocacion(self.inv, self.man)
        self.assertTrue(res.ejecutado)
        self.assertFalse(res.exito)
        self.assertEqual(res.codigo_salida, 2)
        self.assertEqual(res.stderr, "boom")

    def test_timeout(self):
        with patch.object(
            ejecutor.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd=["echo"], timeout=10),
        ):
            res = ejecutor.ejecutar_invocacion(self.inv, self.man)
        self.assertTrue(res.timeout)
        self.assertTrue(res.ejecutado)
        self.assertIsNone(res.codigo_salida)
        self.assertIn("timeout", res.error)

    def test_comando_no_encontrado(self):
        with patch.object(ejecutor.subprocess, "run",
                          side_effect=FileNotFoundError()):
            res = ejecutor.ejecutar_invocacion(self.inv, self.man)
        self.assertFalse(res.ejecutado)
        self.assertIn("no encontrado", res.motivo_no_ejecucion)

    def test_dry_run_no_lanza(self):
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            res = ejecutor.ejecutar_invocacion(self.inv, self.man, dry_run=True)
        mock_run.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("dry-run", res.motivo_no_ejecucion)
        self.assertEqual(res.comando, ("echo", "hola"))

    def test_bloqueado_no_lanza(self):
        man = _manifiesto(operaciones=(_op_lectura(),), requiere_sudo=True)
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            res = ejecutor.ejecutar_invocacion(self.inv, man)
        mock_run.assert_not_called()
        self.assertTrue(res.bloqueado)
        self.assertFalse(res.ejecutado)

    def test_requiere_confirmacion_sin_confirmador_no_lanza(self):
        man = _manifiesto(operaciones=(_op_escritura(),))
        inv = {**self.inv, "nombre_operacion": "borrar",
               "peligrosidad_efectiva": "escritura_local",
               "requiere_confirmacion": True}
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            res = ejecutor.ejecutar_invocacion(inv, man)
        mock_run.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("confirmacion", res.motivo_no_ejecucion)

    def test_confirmador_rechaza_no_lanza(self):
        man = _manifiesto(operaciones=(_op_escritura(),))
        inv = {**self.inv, "nombre_operacion": "borrar",
               "peligrosidad_efectiva": "escritura_local",
               "requiere_confirmacion": True}
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            res = ejecutor.ejecutar_invocacion(
                inv, man, confirmador=lambda _texto: False,
            )
        mock_run.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("no confirmo", res.motivo_no_ejecucion)

    def test_confirmador_acepta_lanza(self):
        man = _manifiesto(operaciones=(_op_escritura(),))
        inv = {**self.inv, "nombre_operacion": "borrar",
               "peligrosidad_efectiva": "escritura_local",
               "requiere_confirmacion": True}
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "ok", "")) as mock_run:
            res = ejecutor.ejecutar_invocacion(
                inv, man, confirmador=lambda _texto: True,
            )
        mock_run.assert_called_once()
        self.assertTrue(res.exito)

    def test_requiere_red_sin_conectividad_no_lanza(self):
        man = _manifiesto(operaciones=(_op_lectura(),), requiere_red=True)
        with patch.object(ejecutor.subprocess, "run") as mock_run:
            res = ejecutor.ejecutar_invocacion(
                self.inv, man, verificador_red=lambda: False,
            )
        mock_run.assert_not_called()
        self.assertFalse(res.ejecutado)
        self.assertIn("conectividad", res.motivo_no_ejecucion)

    def test_requiere_red_con_conectividad_lanza(self):
        man = _manifiesto(operaciones=(_op_lectura(),), requiere_red=True)
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "ok", "")) as mock_run:
            res = ejecutor.ejecutar_invocacion(
                self.inv, man, verificador_red=lambda: True,
            )
        mock_run.assert_called_once()
        self.assertTrue(res.exito)

    def test_salida_se_trunca(self):
        enorme = "x" * (ejecutor._MAX_CAPTURA_CHARS + 500)
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, enorme, "")):
            res = ejecutor.ejecutar_invocacion(self.inv, self.man)
        self.assertTrue(res.truncado)
        self.assertLess(len(res.stdout), len(enorme))


# -- Tests de composicion ------------------------------------------------------


class TestComposicion(unittest.TestCase):

    def setUp(self):
        self.man = _manifiesto(operaciones=(_op_lectura(),))
        self.registro = {"eco_test": self.man}

    def _paso(self, *, parar: bool) -> dict:
        return {
            "clave_automatizacion": "eco_test", "nombre_operacion": "saludar",
            "argumentos": {}, "peligrosidad_efectiva": "lectura",
            "requiere_confirmacion": False, "bloquea_terminal": False,
            "parar_si_falla": parar,
        }

    def test_cadena_completa_exito(self):
        comp = {"razon": "dos pasos",
                "pasos": [self._paso(parar=True), self._paso(parar=True)]}
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "ok", "")) as mock_run:
            res = ejecutor.ejecutar_composicion(comp, self.registro)
        self.assertEqual(mock_run.call_count, 2)
        self.assertTrue(res.exito)
        self.assertFalse(res.abortada)

    def test_parar_si_falla_aborta_la_cadena(self):
        comp = {"razon": "falla el primero",
                "pasos": [self._paso(parar=True), self._paso(parar=True)]}
        # El primer paso devuelve codigo != 0 -> aborta antes del segundo.
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(1, "", "fallo")) as mock_run:
            res = ejecutor.ejecutar_composicion(comp, self.registro)
        self.assertEqual(mock_run.call_count, 1)
        self.assertTrue(res.abortada)
        self.assertEqual(res.paso_fallido, 0)
        self.assertEqual(len(res.resultados), 1)

    def test_sin_parar_si_falla_continua(self):
        comp = {"razon": "no para",
                "pasos": [self._paso(parar=False), self._paso(parar=True)]}
        # Paso 0 falla (parar=False) y paso 1 tiene exito: la cadena NO se
        # aborta pese al fallo del primero, pero el exito global es False.
        with patch.object(
            ejecutor.subprocess, "run",
            side_effect=[_proc(1, "", "fallo"), _proc(0, "ok", "")],
        ) as mock_run:
            res = ejecutor.ejecutar_composicion(comp, self.registro)
        self.assertEqual(mock_run.call_count, 2)
        self.assertFalse(res.abortada)
        self.assertFalse(res.exito)


# -- Tests de pipeline validador -> ejecutor -----------------------------------


class TestIntegracionValidadorEjecutor(unittest.TestCase):
    """Verifica que la salida normalizada del validador alimenta al
    ejecutor sin adaptadores intermedios (contrato de nombres estable)."""

    def test_invocar_validado_se_ejecuta(self):
        man = _manifiesto(
            operaciones=(_op_lectura(nombre="mostrar", flags=("--todo",)),),
        )
        registro = {"eco_test": man}
        decision = {
            "decision": "invocar",
            "clave_automatizacion": "eco_test",
            "nombre_operacion": "mostrar",
            "argumentos": {},
            "razon": "el usuario quiere verlo todo",
        }
        ok, motivo, norm = validar_decision(decision, registro)
        self.assertTrue(ok, motivo)
        with patch.object(ejecutor.subprocess, "run",
                          return_value=_proc(0, "salida", "")) as mock_run:
            res = ejecutor.ejecutar_invocacion(norm, man)
        mock_run.assert_called_once()
        self.assertTrue(res.exito)
        self.assertEqual(res.comando, ("echo", "--todo"))


# -- Tests de trazas -----------------------------------------------------------


class TestTrazas(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self._tmp.name) / "trazas.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_escribir_y_leer(self):
        res = SimpleNamespace(
            clave_automatizacion="eco_test", nombre_operacion="saludar",
            ejecutado=True, codigo_salida=0, timeout=False, bloqueado=False,
            motivo_no_ejecucion=None, error=None,
        )
        ok = trazas.registrar_turno(
            peticion_usuario="hola", decision="invocar", valida=True,
            turno_id="abc123", resultados=[res], ruta=self.ruta,
        )
        self.assertTrue(ok)
        leidas = trazas.leer_trazas(ruta=self.ruta)
        self.assertEqual(len(leidas), 1)
        t = leidas[0]
        self.assertEqual(t["peticion"], "hola")
        self.assertEqual(t["decision"], "invocar")
        self.assertEqual(t["turno_id"], "abc123")
        self.assertEqual(len(t["ejecuciones"]), 1)
        self.assertEqual(t["ejecuciones"][0]["codigo_salida"], 0)

    def test_append_acumula(self):
        for i in range(3):
            trazas.registrar_turno(
                peticion_usuario=f"p{i}", decision="responder", valida=True,
                ruta=self.ruta,
            )
        self.assertEqual(len(trazas.leer_trazas(ruta=self.ruta)), 3)

    def test_limite_devuelve_ultimas(self):
        for i in range(5):
            trazas.registrar_turno(
                peticion_usuario=f"p{i}", decision="responder", valida=True,
                ruta=self.ruta,
            )
        ultimas = trazas.leer_trazas(ruta=self.ruta, limite=2)
        self.assertEqual(len(ultimas), 2)
        self.assertEqual(ultimas[0]["peticion"], "p3")
        self.assertEqual(ultimas[1]["peticion"], "p4")

    def test_lector_tolera_lineas_corruptas(self):
        self.ruta.write_text(
            '{"decision": "responder", "valida": true}\n'
            'esto no es json\n'
            '{"decision": "invocar", "valida": false}\n',
            encoding="utf-8",
        )
        leidas = trazas.leer_trazas(ruta=self.ruta)
        self.assertEqual(len(leidas), 2)

    def test_fichero_inexistente_devuelve_vacio(self):
        self.assertEqual(
            trazas.leer_trazas(ruta=Path(self._tmp.name) / "no_existe.jsonl"),
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
