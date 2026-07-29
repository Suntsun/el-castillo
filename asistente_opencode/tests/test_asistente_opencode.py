#!/usr/bin/env python3
"""Tests para asistente_opencode — N-003."""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from asistente_opencode.asistente_opencode import (
    ejecutar,
    construir_comando,
    RUTA_BASE,
    AGENTE_LECTURA,
)


class TestConstruirComando(TestCase):
    """R3-003: construir_comando elige --dir según si el fichero está dentro o fuera de RUTA_BASE."""

    def test_explicar_fuera_de_ruta_base_usa_parent(self):
        """Para 'explicar' con fichero en /tmp (fuera de RUTA_BASE), --dir = padre del fichero."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            cmd, _ = construir_comando("explicar", f.name)
        idx = cmd.index("--dir")
        self.assertEqual(cmd[idx + 1], "/tmp")

    def test_analizar_fuera_de_ruta_base_usa_parent(self):
        """Para 'analizar' con fichero en /tmp (fuera de RUTA_BASE), --dir = padre del fichero."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            cmd, _ = construir_comando("analizar", f.name)
        idx = cmd.index("--dir")
        self.assertEqual(cmd[idx + 1], "/tmp")

    def test_explicar_dentro_de_ruta_base_usa_ruta_base(self):
        """Para 'explicar' con fichero dentro de RUTA_BASE, --dir = RUTA_BASE (acceso a comun/)."""
        import tempfile
        ruta_base_path = Path(RUTA_BASE)
        with tempfile.NamedTemporaryFile(suffix=".py", dir=str(ruta_base_path), delete=False) as f:
            nombre_tmp = f.name
        try:
            cmd, _ = construir_comando("explicar", nombre_tmp)
            idx = cmd.index("--dir")
            self.assertEqual(cmd[idx + 1], RUTA_BASE)
        finally:
            Path(nombre_tmp).unlink(missing_ok=True)

    def test_analizar_dentro_de_ruta_base_usa_ruta_base(self):
        """Para 'analizar' con fichero dentro de RUTA_BASE, --dir = RUTA_BASE."""
        import tempfile
        ruta_base_path = Path(RUTA_BASE)
        with tempfile.NamedTemporaryFile(suffix=".py", dir=str(ruta_base_path), delete=False) as f:
            nombre_tmp = f.name
        try:
            cmd, _ = construir_comando("analizar", nombre_tmp)
            idx = cmd.index("--dir")
            self.assertEqual(cmd[idx + 1], RUTA_BASE)
        finally:
            Path(nombre_tmp).unlink(missing_ok=True)

    def test_buscar_usa_cwd_como_dir(self):
        """Para 'buscar', --dir es el cwd (no RUTA_BASE)."""
        cmd, _ = construir_comando("buscar", "donde se valida el token")
        idx = cmd.index("--dir")
        # Solo verificamos que --dir existe; su valor puede ser cualquier directorio.
        self.assertTrue(Path(cmd[idx + 1]).is_dir())

    def test_explicar_fichero_inexistente_sale_2(self):
        """Si el fichero no existe, construir_comando hace sys.exit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            construir_comando("explicar", "/tmp/fichero_que_no_existe_xyz.py")
        self.assertEqual(ctx.exception.code, 2)

    def test_resumir_con_fichero_existente_sale_2(self):
        """R3-004: resumir con un fichero existente (no dir) sale con código 2 y mensaje orientativo."""
        import tempfile
        import io
        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            with self.assertRaises(SystemExit) as ctx:
                with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                    construir_comando("resumir", f.name)
        self.assertEqual(ctx.exception.code, 2)

    def test_resumir_con_directorio_existente_ok(self):
        """R3-004: resumir con un directorio existente construye el comando sin error."""
        cmd, _ = construir_comando("resumir", "/tmp")
        self.assertIn("--dir", cmd)


class TestEjecutarPropagaRespuesta(TestCase):
    """N-003: ejecutar propaga la respuesta de OpenCode si no está vacía."""

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_ejecutar_propaga_texto_no_vacio(self, mock_parsear, mock_run):
        """Con respuesta simulada no vacía, ejecutar devuelve 0 y muestra el texto."""
        import tempfile
        mock_run.return_value = MagicMock(returncode=0, stdout="json...", stderr="")
        mock_parsear.return_value = ("Respuesta del modelo con contenido.", "ses_xxx")

        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            import io
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                rc = ejecutar("explicar", f.name)
        self.assertEqual(rc, 0)
        self.assertIn("Respuesta del modelo con contenido.", mock_out.getvalue())

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_ejecutar_devuelve_1_si_respuesta_vacia(self, mock_parsear, mock_run):
        """Con respuesta vacía, ejecutar devuelve 1."""
        import tempfile
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_parsear.return_value = (None, None)

        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            rc = ejecutar("explicar", f.name)
        self.assertEqual(rc, 1)


# ── R6-001: buscar "" y "   " → exit≠0 sin invocar OpenCode ──────────────────

class TestR6001BuscarVacio(TestCase):
    """R6-001: consulta vacía o solo espacios → sys.exit(2), sin invocar OpenCode."""

    def test_buscar_vacio_sale_2(self):
        """buscar '' → sys.exit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            construir_comando("buscar", "")
        self.assertEqual(ctx.exception.code, 2)

    def test_buscar_solo_espacios_sale_2(self):
        """buscar '   ' → sys.exit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            construir_comando("buscar", "   ")
        self.assertEqual(ctx.exception.code, 2)

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    def test_buscar_vacio_no_invoca_opencode(self, mock_run):
        """buscar '' → OpenCode no se invoca."""
        with self.assertRaises(SystemExit):
            construir_comando("buscar", "")
        mock_run.assert_not_called()

    def test_buscar_consulta_valida_no_sale(self):
        """buscar 'algo real' → construye comando sin sys.exit (camino feliz)."""
        cmd, descripcion = construir_comando("buscar", "algo real")
        self.assertIn("--dir", cmd)
        self.assertIn("algo real", descripcion)


# ── Heraldo: spinner medieval durante la espera bloqueante ──────────────────

class TestHeraldoSpinner(TestCase):
    """El spinner del Heraldo envuelve la espera de OpenCode pero:
    - en no-TTY es no-op total (sin frames, salida funcional intacta),
    - la llamada a opencode se sigue haciendo igual y el resultado se propaga,
    - solo se activa (lanza hilo) cuando stdout es un TTY.
    """

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_no_tty_salida_funcional_sin_frames_spinner(self, mock_parsear, mock_run):
        """En no-TTY (StringIO no es TTY): se muestra el texto y NO hay glifos de spinner."""
        import tempfile
        import io
        mock_run.return_value = MagicMock(returncode=0, stdout="json...", stderr="")
        mock_parsear.return_value = ("Respuesta funcional intacta.", "ses_x")

        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                rc = ejecutar("explicar", f.name)
            salida = mock_out.getvalue()

        self.assertEqual(rc, 0)
        self.assertIn("Respuesta funcional intacta.", salida)
        # Ningún glifo del spinner medieval debe aparecer en no-TTY. (No se
        # comprueban los glifos clásicos | / - \ porque coinciden con caracteres
        # legítimos de rutas; en su lugar verificamos los marcadores del Heraldo.)
        for glifo in "✶✦✧✩✪✫":
            self.assertNotIn(glifo, salida)
        # Tampoco la secuencia ANSI de borrado de línea ni el atributo dim del Heraldo.
        self.assertNotIn("\033[2K", salida)
        self.assertNotIn("\033[2m", salida)

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_no_tty_no_lanza_hilo_spinner(self, mock_parsear, mock_run):
        """En no-TTY, el Heraldo no lanza el hilo del spinner (no-op total)."""
        import tempfile
        import io
        mock_run.return_value = MagicMock(returncode=0, stdout="json...", stderr="")
        mock_parsear.return_value = ("ok", "ses_x")

        with patch("comun.heraldo.threading.Thread") as mock_thread:
            with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
                with patch("sys.stdout", new_callable=io.StringIO):
                    ejecutar("explicar", f.name)
        mock_thread.assert_not_called()

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_resultado_opencode_se_propaga_sin_alterar(self, mock_parsear, mock_run):
        """El texto devuelto por opencode se propaga tal cual (con el envoltorio del Heraldo)."""
        import tempfile
        import io
        texto_modelo = "Texto exacto del modelo, sin alterar por el Heraldo."
        mock_run.return_value = MagicMock(returncode=0, stdout="json...", stderr="")
        mock_parsear.return_value = (texto_modelo, "ses_x")

        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                rc = ejecutar("explicar", f.name)
            salida = mock_out.getvalue()

        self.assertEqual(rc, 0)
        # opencode se invocó exactamente una vez con los mismos parámetros clave.
        self.assertEqual(mock_run.call_count, 1)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("capture_output"))
        self.assertEqual(kwargs.get("timeout"), 240)
        # El texto del modelo aparece intacto en la salida.
        self.assertIn(texto_modelo, salida)

    @patch("asistente_opencode.asistente_opencode.subprocess.run")
    @patch("asistente_opencode.asistente_opencode._parsear_stream_ndjson")
    def test_en_tty_se_activa_el_spinner(self, mock_parsear, mock_run):
        """Con stdout simulado como TTY, el Heraldo SÍ lanza el hilo del spinner."""
        import tempfile
        mock_run.return_value = MagicMock(returncode=0, stdout="json...", stderr="")
        mock_parsear.return_value = ("ok", "ses_x")

        # stdout falso que se declara TTY; capturamos lo que escriba el spinner.
        class _FakeTTY:
            def __init__(self):
                self.buffer = []
            def isatty(self):
                return True
            def write(self, s):
                self.buffer.append(s)
            def flush(self):
                pass

        fake = _FakeTTY()
        with tempfile.NamedTemporaryFile(suffix=".py", dir="/tmp") as f:
            with patch("comun.heraldo.threading.Thread") as mock_thread:
                with patch("sys.stdout", fake):
                    rc = ejecutar("explicar", f.name)
        self.assertEqual(rc, 0)
        # En TTY el Heraldo arranca su hilo cosmético.
        mock_thread.assert_called_once()


if __name__ == "__main__":
    main()
