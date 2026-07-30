#!/usr/bin/env python3
"""Tests para oraculo_errores."""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oraculo_errores import (
    detectar_lenguaje,
    extraer_info,
    buscar_explicacion,
    analizar,
    leer_portapapeles,
    leer_archivo,
    leer_stdin,
    formatear_resultado,
    _fallback_llm,
)


# ── Tests de detección de lenguaje ───────────────────────────────


class TestDetectarLenguaje(TestCase):
    def test_detecta_python_traceback(self):
        texto = """Traceback (most recent call last):
  File "app.py", line 10, in <module>
    import foobar
ModuleNotFoundError: No module named 'foobar'"""
        self.assertEqual(detectar_lenguaje(texto), "Python")

    def test_detecta_python_file_pattern(self):
        texto = 'File "main.py", line 5\n  print(x\n       ^\nSyntaxError: unexpected EOF'
        self.assertEqual(detectar_lenguaje(texto), "Python")

    def test_detecta_node(self):
        texto = """TypeError: Cannot read properties of undefined (reading 'map')
    at Object.<anonymous> (/app/src/api.js:42:15)
    at Module._compile (node:internal/modules/cjs/loader:1241:14)"""
        self.assertEqual(detectar_lenguaje(texto), "JavaScript/Node")

    def test_detecta_node_modules(self):
        texto = "Error: something failed\n    at /home/user/node_modules/express/lib/router.js:46:12"
        self.assertEqual(detectar_lenguaje(texto), "JavaScript/Node")

    def test_detecta_rust(self):
        texto = "thread 'main' panicked at 'called `Option::unwrap()' on a `None` value', src/main.rs:10:5"
        self.assertEqual(detectar_lenguaje(texto), "Rust")

    def test_detecta_java(self):
        texto = """Exception in thread "main" java.lang.NullPointerException
    at com.example.App.main(App.java:15)"""
        self.assertEqual(detectar_lenguaje(texto), "Java")

    def test_detecta_pacman(self):
        texto = "error: target not found: paquete-fantasma\n:: pacman -S paquete-fantasma"
        self.assertEqual(detectar_lenguaje(texto), "Arch/pacman")

    def test_detecta_systemd(self):
        texto = "systemctl status nginx.service\n  Active: failed\n  Main process exited, code=exited, status=1"
        self.assertEqual(detectar_lenguaje(texto), "systemd")

    def test_detecta_git(self):
        texto = "fatal: not a git repository (or any parent up to mount point /)"
        self.assertEqual(detectar_lenguaje(texto), "Git")

    def test_fallback_general(self):
        texto = "something went wrong with the flux capacitor"
        self.assertEqual(detectar_lenguaje(texto), "General")

    def test_detecta_node_por_tipo_error(self):
        texto = "TypeError: Cannot read properties of undefined (reading 'x')"
        self.assertEqual(detectar_lenguaje(texto), "JavaScript/Node")


# ── Tests de extracción de info ──────────────────────────────────


class TestExtraerInfo(TestCase):
    def test_extrae_python(self):
        texto = """Traceback (most recent call last):
  File "/home/user/app.py", line 42, in main
    result = process(data)
  File "/home/user/lib.py", line 10, in process
    return data["key"]
KeyError: 'key'"""
        info = extraer_info(texto, "Python")
        self.assertEqual(info["error_tipo"], "KeyError")
        self.assertEqual(info["mensaje"], "'key'")
        self.assertEqual(info["archivo"], "/home/user/lib.py")
        self.assertEqual(info["linea"], "10")

    def test_extrae_node(self):
        texto = """TypeError: Cannot read properties of undefined (reading 'map')
    at handler (/app/src/api.js:42:15)
    at Layer.handle (/app/node_modules/express/lib/router.js:89:5)"""
        info = extraer_info(texto, "JavaScript/Node")
        self.assertEqual(info["error_tipo"], "TypeError")
        self.assertIn("map", info["mensaje"])
        self.assertEqual(info["archivo"], "/app/src/api.js")
        self.assertEqual(info["linea"], "42")

    def test_extrae_rust_panic(self):
        texto = "thread 'main' panicked at 'index out of bounds', src/main.rs:15:10"
        info = extraer_info(texto, "Rust")
        self.assertEqual(info["error_tipo"], "panic")
        self.assertEqual(info["archivo"], "src/main.rs")
        self.assertEqual(info["linea"], "15")

    def test_extrae_java(self):
        texto = """Exception in thread "main" java.lang.NullPointerException
    at com.example.App.main(App.java:15)"""
        info = extraer_info(texto, "Java")
        self.assertEqual(info["error_tipo"], "java.lang.NullPointerException")
        self.assertEqual(info["archivo"], "App.java")
        self.assertEqual(info["linea"], "15")

    def test_extrae_pacman(self):
        texto = "error: target not found: nonexistent-package"
        info = extraer_info(texto, "Arch/pacman")
        self.assertEqual(info["error_tipo"], "pacman error")
        self.assertIn("target not found", info["mensaje"])

    def test_extrae_git(self):
        texto = "fatal: not a git repository (or any parent up to mount point /)"
        info = extraer_info(texto, "Git")
        self.assertEqual(info["error_tipo"], "git error")
        self.assertIn("not a git repository", info["mensaje"])

    def test_extrae_archivo_linea_python(self):
        texto = 'File "/home/usuario/test.py", line 99, in foo\n    bar()\nNameError: name \'bar\' is not defined'
        info = extraer_info(texto, "Python")
        self.assertEqual(info["archivo"], "/home/usuario/test.py")
        self.assertEqual(info["linea"], "99")


# ── Tests de búsqueda de explicación ─────────────────────────────


class TestBuscarExplicacion(TestCase):
    def test_python_module_not_found(self):
        texto = "ModuleNotFoundError: No module named 'requests'"
        resultado = buscar_explicacion(texto, "Python")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("requests", causa)
        self.assertIn("pip install", fix)

    def test_python_key_error(self):
        texto = "KeyError: 'username'"
        resultado = buscar_explicacion(texto, "Python")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("username", causa)
        self.assertIn(".get(", fix)

    def test_node_undefined(self):
        texto = "TypeError: Cannot read properties of undefined (reading 'map')"
        resultado = buscar_explicacion(texto, "JavaScript/Node")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("undefined", causa.lower())

    def test_node_eaddrinuse(self):
        texto = "Error: listen EADDRINUSE :::3000"
        resultado = buscar_explicacion(texto, "JavaScript/Node")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("3000", causa)
        self.assertIn("lsof", fix)

    def test_rust_borrow(self):
        texto = "error[E0382]: borrow of moved value: `x`"
        resultado = buscar_explicacion(texto, "Rust")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("moved", causa.lower())

    def test_pacman_target_not_found(self):
        texto = "error: target not found: foobar-pkg"
        resultado = buscar_explicacion(texto, "Arch/pacman")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("foobar-pkg", causa)
        self.assertIn("AUR", fix)

    def test_pacman_db_lock(self):
        texto = "error: unable to lock database\nerror: could not remove db.lck"
        resultado = buscar_explicacion(texto, "Arch/pacman")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("rm", fix)

    def test_systemd_failed_to_start(self):
        texto = "Failed to start nginx.service"
        resultado = buscar_explicacion(texto, "systemd")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("nginx.service", causa)
        self.assertIn("journalctl", fix)

    def test_git_not_a_repo(self):
        texto = "fatal: not a git repository"
        resultado = buscar_explicacion(texto, "Git")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("git init", fix)

    def test_general_permission_denied(self):
        texto = "Permission denied: /etc/shadow"
        resultado = buscar_explicacion(texto, "General")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("permisos", causa.lower())

    def test_general_no_space(self):
        texto = "OSError: No space left on device"
        resultado = buscar_explicacion(texto, "General")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("lleno", causa.lower())

    def test_sin_coincidencia(self):
        texto = "todo funciona bien, no hay error"
        resultado = buscar_explicacion(texto, "General")
        self.assertIsNone(resultado)

    def test_general_fallback_desde_lenguaje(self):
        """Un error general se encuentra aunque el lenguaje sea Python."""
        texto = "Segmentation fault (core dumped)"
        resultado = buscar_explicacion(texto, "Python")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertIn("memoria", causa.lower())


# ── Tests de análisis completo ───────────────────────────────────


class TestAnalizar(TestCase):
    def test_analisis_completo_python(self):
        texto = """Traceback (most recent call last):
  File "app.py", line 5, in <module>
    import nonexistent
ModuleNotFoundError: No module named 'nonexistent'"""
        info, lenguaje, causa, fix, desde_llm = analizar(texto)
        self.assertEqual(lenguaje, "Python")
        self.assertEqual(info["error_tipo"], "ModuleNotFoundError")
        self.assertIn("nonexistent", causa)
        self.assertIn("pip install", fix)
        self.assertFalse(desde_llm)

    def test_analisis_completo_node(self):
        texto = """ReferenceError: myVar is not defined
    at Object.<anonymous> (/app/index.js:10:1)"""
        info, lenguaje, causa, fix, desde_llm = analizar(texto)
        self.assertEqual(lenguaje, "JavaScript/Node")
        self.assertIn("myVar", causa)
        self.assertFalse(desde_llm)


# ── Tests de entrada ─────────────────────────────────────────────


class TestLeerPortapapeles(TestCase):
    @patch("oraculo_errores.shutil.which")
    def test_sin_wl_paste(self, mock_which):
        mock_which.return_value = None
        resultado = leer_portapapeles()
        self.assertIsNone(resultado)

    @patch("oraculo_errores.subprocess.run")
    @patch("oraculo_errores.shutil.which")
    def test_portapapeles_ok(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/wl-paste"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="TypeError: x is not defined\n",
            stderr="",
        )
        resultado = leer_portapapeles()
        self.assertEqual(resultado, "TypeError: x is not defined")

    @patch("oraculo_errores.subprocess.run")
    @patch("oraculo_errores.shutil.which")
    def test_portapapeles_vacio(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/wl-paste"
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="   \n",
            stderr="",
        )
        resultado = leer_portapapeles()
        self.assertIsNone(resultado)


class TestLeerStdin(TestCase):
    @patch("oraculo_errores.sys.stdin")
    def test_stdin_con_datos(self, mock_stdin):
        mock_stdin.isatty.return_value = False
        mock_stdin.read.return_value = "SyntaxError: invalid syntax\n"
        resultado = leer_stdin()
        self.assertEqual(resultado, "SyntaxError: invalid syntax")

    @patch("oraculo_errores.sys.stdin")
    def test_stdin_es_tty(self, mock_stdin):
        mock_stdin.isatty.return_value = True
        resultado = leer_stdin()
        self.assertIsNone(resultado)


class TestLeerArchivo(TestCase):
    def test_leer_archivo_existente(self, ):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("KeyError: 'missing_key'\n")
            f.flush()
            resultado = leer_archivo(f.name)
        self.assertEqual(resultado, "KeyError: 'missing_key'")
        Path(f.name).unlink()

    def test_leer_archivo_inexistente(self):
        resultado = leer_archivo("/tmp/no_existe_nunca_jamas.log")
        self.assertIsNone(resultado)


# ── Tests de formato de salida ───────────────────────────────────


class TestFormatearResultado(TestCase):
    def test_formato_completo(self):
        info = {
            "error_tipo": "TypeError",
            "mensaje": "x is undefined",
            "archivo": "app.js",
            "linea": "42",
        }
        salida = formatear_resultado(info, "JavaScript/Node", "Variable undefined", "Usa optional chaining")
        self.assertIn("TypeError", salida)
        self.assertIn("JavaScript/Node", salida)
        self.assertIn("app.js", salida)
        self.assertIn("42", salida)
        self.assertIn("Variable undefined", salida)
        self.assertIn("optional chaining", salida)

    def test_formato_sin_causa(self):
        info = {"error_tipo": "MysteryError", "mensaje": None, "archivo": None, "linea": None}
        salida = formatear_resultado(info, "General", None, None)
        self.assertIn("MysteryError", salida)
        self.assertIn("No se encontr", salida)


# ── Tests de fallback LLM ───────────────────────────────────────


class TestFallbackLLM(TestCase):
    @patch("oraculo_errores.llm_disponible", return_value=False)
    def test_llm_no_disponible_retorna_none(self, _mock_disp):
        """Si Ollama no está corriendo, _fallback_llm devuelve None."""
        resultado = _fallback_llm("AlgunErrorRaro: cosas inexplicables")
        self.assertIsNone(resultado)

    @patch("oraculo_errores.consultar_llm", return_value="El error indica X.\nPrueba hacer Y para solucionarlo.")
    @patch("oraculo_errores.llm_disponible", return_value=True)
    def test_llm_responde_con_causa_y_fix(self, _mock_disp, _mock_llm):
        """Si el LLM responde, se separa en causa y fix."""
        resultado = _fallback_llm("AlgunErrorRaro: cosas inexplicables")
        self.assertIsNotNone(resultado)
        causa, fix = resultado
        self.assertEqual(causa, "El error indica X.")
        self.assertIn("Prueba hacer Y", fix)

    @patch("oraculo_errores.consultar_llm", return_value=None)
    @patch("oraculo_errores.llm_disponible", return_value=True)
    def test_llm_disponible_pero_sin_respuesta(self, _mock_disp, _mock_llm):
        """Si Ollama está corriendo pero no devuelve respuesta, retorna None."""
        resultado = _fallback_llm("AlgunErrorRaro: cosas inexplicables")
        self.assertIsNone(resultado)


class TestAnalizarConLLM(TestCase):
    @patch("oraculo_errores._fallback_llm", return_value=("Causa del LLM", "Fix del LLM"))
    def test_analizar_usa_llm_cuando_no_hay_patron(self, _mock_fb):
        """Si no hay patrón conocido, analizar recurre al LLM."""
        texto = "FrobnitzError: el frobnitz se desalineó completamente"
        info, lenguaje, causa, fix, desde_llm = analizar(texto)
        self.assertTrue(desde_llm)
        self.assertEqual(causa, "Causa del LLM")
        self.assertEqual(fix, "Fix del LLM")

    @patch("oraculo_errores._fallback_llm", return_value=None)
    def test_analizar_sin_patron_ni_llm(self, _mock_fb):
        """Sin patrón y sin LLM, causa y fix son None."""
        texto = "FrobnitzError: el frobnitz se desalineó completamente"
        info, lenguaje, causa, fix, desde_llm = analizar(texto)
        self.assertFalse(desde_llm)
        self.assertIsNone(causa)
        self.assertIsNone(fix)


class TestFormatearConLLM(TestCase):
    def test_formato_con_etiqueta_llm(self):
        """Cuando desde_llm=True, las etiquetas muestran [LLM]."""
        info = {"error_tipo": "FrobnitzError", "mensaje": "desalineado", "archivo": None, "linea": None}
        salida = formatear_resultado(info, "General", "Causa LLM", "Fix LLM", desde_llm=True)
        self.assertIn("[LLM]", salida)
        self.assertIn("CAUSA [LLM]:", salida)
        self.assertIn("FIX [LLM]:", salida)

    def test_formato_sin_etiqueta_llm(self):
        """Cuando desde_llm=False (default), no aparece [LLM]."""
        info = {"error_tipo": "KeyError", "mensaje": "'x'", "archivo": None, "linea": None}
        salida = formatear_resultado(info, "Python", "La clave no existe", "Usa .get()")
        self.assertNotIn("[LLM]", salida)


if __name__ == "__main__":
    main()
