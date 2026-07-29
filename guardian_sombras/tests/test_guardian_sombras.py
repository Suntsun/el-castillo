#!/usr/bin/env python3
"""Tests para guardian_sombras — Detector de Secretos."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardian_sombras import (
    _compilar_patrones,
    escanear_contenido,
    cargar_whitelist,
    esta_en_whitelist,
    escanear_staged,
    escanear_working_tree,
    Hallazgo,
    ResultadoEscaneo,
)


# Config completa con todos los patrones activados
CONFIG_COMPLETA = {
    "patrones": {
        "aws_keys": True,
        "tokens_genericos": True,
        "claves_privadas": True,
        "jwt": True,
        "credenciales_url": True,
        "archivos_env": True,
        "github_tokens": True,
        "slack_tokens": True,
        "discord_webhooks": True,
        "stripe_keys": True,
        "db_connection_strings": True,
        "google_api_keys": True,
        "telegram_tokens": True,
    },
    "notificacion": {"duracion": 5000, "severidad": "error"},
}

TOTAL_PATRONES = 12


class TestCompilarPatrones(unittest.TestCase):
    """Verifica que los patrones se compilan segun la config."""

    def test_todos_activados(self):
        patrones = _compilar_patrones(CONFIG_COMPLETA)
        self.assertEqual(len(patrones), TOTAL_PATRONES)
        self.assertIn("aws_keys", patrones)
        self.assertIn("tokens_genericos", patrones)
        self.assertIn("claves_privadas", patrones)
        self.assertIn("jwt", patrones)
        self.assertIn("credenciales_url", patrones)
        self.assertIn("github_tokens", patrones)

    def test_desactivar_patron(self):
        config = {"patrones": {"aws_keys": False, "jwt": False}}
        patrones = _compilar_patrones(config)
        self.assertNotIn("aws_keys", patrones)
        self.assertNotIn("jwt", patrones)
        self.assertIn("tokens_genericos", patrones)

    def test_config_vacia(self):
        patrones = _compilar_patrones({})
        # Todos activados por defecto
        self.assertEqual(len(patrones), TOTAL_PATRONES)


class TestDeteccionAWSKeys(unittest.TestCase):
    """Detecta claves AWS Access Key ID."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_aws_key(self):
        contenido = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'
        hallazgos = escanear_contenido(contenido, "config.py", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Clave AWS", tipos)

    def test_no_detecta_texto_normal(self):
        contenido = "Esto es un texto normal sin secretos"
        hallazgos = escanear_contenido(contenido, "readme.md", self.patrones, [])
        self.assertEqual(len(hallazgos), 0)


class TestDeteccionTokensGenericos(unittest.TestCase):
    """Detecta tokens, passwords y API keys genericos."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_password_equals(self):
        contenido = 'password = "mi_contrasenya_super_secreta"'
        hallazgos = escanear_contenido(contenido, "db.py", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Token/password generico", tipos)

    def test_detecta_api_key_colon(self):
        contenido = 'api_key: "abcdefgh12345678"'
        hallazgos = escanear_contenido(contenido, "settings.yaml", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Token/password generico", tipos)

    def test_detecta_secret_key(self):
        contenido = "SECRET_KEY = super_secret_value_here"
        hallazgos = escanear_contenido(contenido, "env.py", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Token/password generico", tipos)

    def test_no_detecta_password_corto(self):
        """No detecta si el valor tiene menos de 8 caracteres."""
        contenido = 'password = "short"'
        hallazgos = escanear_contenido(contenido, "test.py", self.patrones, [])
        # password = "short" tiene valor "short" (5 chars) -> no deberia detectar
        tipos = [h.tipo for h in hallazgos]
        self.assertNotIn("Token/password generico", tipos)


class TestDeteccionClavesPrivadas(unittest.TestCase):
    """Detecta bloques de clave privada."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_rsa_key(self):
        contenido = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."
        hallazgos = escanear_contenido(contenido, "key.pem", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Clave privada", tipos)

    def test_detecta_ec_key(self):
        contenido = "-----BEGIN EC PRIVATE KEY-----"
        hallazgos = escanear_contenido(contenido, "ec.pem", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Clave privada", tipos)


class TestDeteccionJWT(unittest.TestCase):
    """Detecta tokens JWT."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        contenido = f'token = "{jwt}"'
        hallazgos = escanear_contenido(contenido, "auth.py", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("JSON Web Token", tipos)

    def test_no_detecta_texto_corto_con_eyj(self):
        contenido = "eyJhb.eyJz.abc"
        hallazgos = escanear_contenido(contenido, "test.py", self.patrones, [])
        # Segmentos demasiado cortos -> no deberia detectar
        jwt_hallazgos = [h for h in hallazgos if h.tipo == "JSON Web Token"]
        self.assertEqual(len(jwt_hallazgos), 0)


class TestDeteccionCredencialesURL(unittest.TestCase):
    """Detecta credenciales embebidas en URLs."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_credenciales_http(self):
        contenido = 'db_url = "https://admin:password123@db.example.com/mydb"'
        hallazgos = escanear_contenido(contenido, "config.py", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("Credenciales en URL", tipos)

    def test_no_detecta_url_sin_credenciales(self):
        contenido = 'url = "https://api.example.com/v1/data"'
        hallazgos = escanear_contenido(contenido, "api.py", self.patrones, [])
        url_hallazgos = [h for h in hallazgos if h.tipo == "Credenciales en URL"]
        self.assertEqual(len(url_hallazgos), 0)


class TestDeteccionGitHubTokens(unittest.TestCase):
    """Detecta GitHub Personal Access Tokens."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_ghp_token(self):
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijkl"
        contenido = f'GITHUB_TOKEN = "{token}"'
        hallazgos = escanear_contenido(contenido, "ci.yml", self.patrones, [])
        tipos = [h.tipo for h in hallazgos]
        self.assertIn("GitHub token", tipos)


class TestWhitelist(unittest.TestCase):
    """Verifica el funcionamiento de la whitelist."""

    def test_esta_en_whitelist(self):
        whitelist = ["AKIAIOSFODNN7EXAMPLE", "test_password_123"]
        self.assertTrue(esta_en_whitelist("AKIAIOSFODNN7EXAMPLE", whitelist))
        self.assertTrue(esta_en_whitelist("token = test_password_123", whitelist))

    def test_no_esta_en_whitelist(self):
        whitelist = ["AKIAIOSFODNN7EXAMPLE"]
        self.assertFalse(esta_en_whitelist("AKIAIOSFODNN7OTRO123", whitelist))

    def test_whitelist_vacia(self):
        self.assertFalse(esta_en_whitelist("cualquier_cosa", []))

    def test_respeta_whitelist_en_escaneo(self):
        patrones = _compilar_patrones(CONFIG_COMPLETA)
        contenido = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"'
        whitelist = ["AKIAIOSFODNN7EXAMPLE"]
        hallazgos = escanear_contenido(contenido, "config.py", patrones, whitelist)
        aws_hallazgos = [h for h in hallazgos if h.tipo == "Clave AWS"]
        self.assertEqual(len(aws_hallazgos), 0)

    @patch("guardian_sombras.Path.exists", return_value=True)
    @patch("guardian_sombras.Path.read_text", return_value="AKIAIOSFODNN7EXAMPLE\n# comentario\ntest_password")
    def test_cargar_whitelist(self, mock_read, mock_exists):
        resultado = cargar_whitelist(Path("/fake/repo"))
        self.assertIn("AKIAIOSFODNN7EXAMPLE", resultado)
        self.assertIn("test_password", resultado)
        # Los comentarios se filtran
        self.assertNotIn("# comentario", resultado)


class TestTextoLimpio(unittest.TestCase):
    """Verifica que texto sin secretos no genera hallazgos."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_codigo_python_normal(self):
        contenido = """
import os
import sys

def main():
    nombre = "usuario"
    datos = [1, 2, 3]
    print(f"Hola {nombre}")

if __name__ == "__main__":
    main()
"""
        hallazgos = escanear_contenido(contenido, "app.py", self.patrones, [])
        self.assertEqual(len(hallazgos), 0)

    def test_yaml_sin_secretos(self):
        contenido = """
server:
  host: localhost
  port: 8080
  debug: true
logging:
  level: INFO
"""
        hallazgos = escanear_contenido(contenido, "config.yaml", self.patrones, [])
        self.assertEqual(len(hallazgos), 0)


class TestEscaneoStaged(unittest.TestCase):
    """Verifica el escaneo de archivos staged."""

    @patch("guardian_sombras.obtener_raiz_repo", return_value=Path("/fake/repo"))
    @patch("guardian_sombras.cargar_whitelist", return_value=[])
    @patch("guardian_sombras.obtener_archivos_staged", return_value=["config.py"])
    @patch("guardian_sombras.obtener_contenido_staged", return_value='password = "super_secreto_largo"')
    def test_detecta_secreto_en_staged(self, mock_cont, mock_arch, mock_wl, mock_raiz):
        resultado = escanear_staged(CONFIG_COMPLETA)
        self.assertTrue(resultado.tiene_secretos)

    @patch("guardian_sombras.obtener_raiz_repo", return_value=Path("/fake/repo"))
    @patch("guardian_sombras.cargar_whitelist", return_value=[])
    @patch("guardian_sombras.obtener_archivos_staged", return_value=[".env"])
    @patch("guardian_sombras.obtener_contenido_staged", return_value="DB_HOST=localhost")
    def test_detecta_env_en_staged(self, mock_cont, mock_arch, mock_wl, mock_raiz):
        resultado = escanear_staged(CONFIG_COMPLETA)
        self.assertTrue(resultado.tiene_secretos)
        self.assertEqual(resultado.hallazgos[0].tipo, "Archivo .env en el commit")

    @patch("guardian_sombras.obtener_raiz_repo", return_value=Path("/fake/repo"))
    @patch("guardian_sombras.cargar_whitelist", return_value=[])
    @patch("guardian_sombras.obtener_archivos_staged", return_value=["main.py"])
    @patch("guardian_sombras.obtener_contenido_staged", return_value='print("hola mundo")')
    def test_staged_limpio(self, mock_cont, mock_arch, mock_wl, mock_raiz):
        resultado = escanear_staged(CONFIG_COMPLETA)
        self.assertFalse(resultado.tiene_secretos)


class TestEscaneoWorkingTree(unittest.TestCase):
    """Verifica el escaneo del working tree completo."""

    @patch("guardian_sombras.obtener_raiz_repo", return_value=Path("/fake/repo"))
    @patch("guardian_sombras.cargar_whitelist", return_value=[])
    @patch("guardian_sombras.obtener_archivos_working_tree", return_value=["db.py"])
    @patch("guardian_sombras.Path.read_text", return_value='secret_key = "abcdefghij_muy_secreto"')
    def test_detecta_secreto_en_working_tree(self, mock_read, mock_arch, mock_wl, mock_raiz):
        resultado = escanear_working_tree(CONFIG_COMPLETA)
        self.assertTrue(resultado.tiene_secretos)

    @patch("guardian_sombras.obtener_raiz_repo", return_value=None)
    def test_sin_repo_sale_con_error(self, mock_raiz):
        # N-004: --scan sin repo debe fallar con exit!=0, NO devolver limpio
        with self.assertRaises(SystemExit) as ctx:
            escanear_working_tree(CONFIG_COMPLETA)
        self.assertNotEqual(ctx.exception.code, 0)


class TestResultadoEscaneo(unittest.TestCase):
    """Verifica el modelo ResultadoEscaneo."""

    def test_sin_hallazgos(self):
        r = ResultadoEscaneo()
        self.assertFalse(r.tiene_secretos)

    def test_con_hallazgos(self):
        r = ResultadoEscaneo(hallazgos=[
            Hallazgo("file.py", 1, "password = xxx", "Token/password generico")
        ])
        self.assertTrue(r.tiene_secretos)


class TestPatronesNuevos(unittest.TestCase):
    """Verifica deteccion de los patrones nuevos."""

    def setUp(self):
        self.patrones = _compilar_patrones(CONFIG_COMPLETA)

    def test_detecta_slack_token(self):
        h = escanear_contenido('SLACK_TOKEN="xoxb-12345678-abcdefgh"', "f.py", self.patrones, [])
        tipos = [x.tipo for x in h]
        self.assertTrue(any("Slack" in t for t in tipos))

    def test_detecta_discord_webhook(self):
        h = escanear_contenido(
            'url = "https://discord.com/api/webhooks/123456/abcdefghijk-xyz"',
            "f.py", self.patrones, [],
        )
        tipos = [x.tipo for x in h]
        self.assertTrue(any("Discord" in t for t in tipos))

    def test_detecta_stripe_key(self):
        h = escanear_contenido('sk_live_abcdefghijklmnopqrstu', "f.py", self.patrones, [])
        tipos = [x.tipo for x in h]
        self.assertTrue(any("Stripe" in t for t in tipos))

    def test_detecta_db_connection_string(self):
        h = escanear_contenido(
            'DB_URL="postgres://user:pass@host:5432/dbname"',
            "f.py", self.patrones, [],
        )
        tipos = [x.tipo for x in h]
        self.assertTrue(any("BD" in t for t in tipos))

    def test_detecta_google_api_key(self):
        h = escanear_contenido(
            'GOOGLE_KEY="AIzaSyA1234567890abcdefghijklmnopqrstuv"',
            "f.py", self.patrones, [],
        )
        tipos = [x.tipo for x in h]
        self.assertTrue(any("Google" in t for t in tipos))

    def test_detecta_telegram_token(self):
        h = escanear_contenido(
            'BOT_TOKEN="1234567890:ABCDefghijklmnopqrstuvwxyz123456789"',
            "f.py", self.patrones, [],
        )
        tipos = [x.tipo for x in h]
        self.assertTrue(any("Telegram" in t for t in tipos))


class TestAmenazas(unittest.TestCase):
    """Verifica el escaneo de amenazas del sistema."""

    def test_escanear_amenazas_retorna_lista(self):
        from guardian_sombras import escanear_amenazas
        resultado = escanear_amenazas()
        self.assertIsInstance(resultado, list)

    def test_check_ssh_sin_archivo(self):
        from guardian_sombras import _check_ssh_autorizado
        with patch("guardian_sombras.Path.home") as mock_home:
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                mock_home.return_value = Path(d)
                resultado = _check_ssh_autorizado()
                self.assertEqual(len(resultado), 0)

    def test_check_cron_sin_crontab(self):
        from guardian_sombras import _check_cron_sospechoso
        with patch("guardian_sombras.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            resultado = _check_cron_sospechoso()
            cron_amenazas = [a for a in resultado if a.categoria == "Cron sospechoso"]
            self.assertEqual(len(cron_amenazas), 0)


class TestScanSinRepo(unittest.TestCase):
    """N-004: --scan y --historial deben fallar con exit!=0 si no hay repo git."""

    @patch("guardian_sombras.obtener_raiz_repo", return_value=None)
    def test_scan_sin_repo_sale_con_error(self, _mock_raiz):
        """escanear_working_tree llama sys.exit(1) cuando no hay repo git."""
        config = {}
        with self.assertRaises(SystemExit) as ctx:
            escanear_working_tree(config)
        self.assertNotEqual(ctx.exception.code, 0)

    @patch("guardian_sombras.obtener_raiz_repo", return_value=None)
    def test_historial_sin_repo_sale_con_error(self, _mock_raiz):
        """escanear_historial llama sys.exit(1) cuando no hay repo git."""
        from guardian_sombras import escanear_historial
        config = {}
        with self.assertRaises(SystemExit) as ctx:
            escanear_historial(config)
        self.assertNotEqual(ctx.exception.code, 0)


# ── R6-003: mostrar_resultado: mensaje neutro en manual vs COMMIT BLOQUEADO en hook ─

class TestR6003MostrarResultado(unittest.TestCase):
    """R6-003: mostrar_resultado usa titulo distinto segun es_hook."""

    def _resultado_con_secreto(self):
        return ResultadoEscaneo(hallazgos=[
            Hallazgo("config.py", 1, 'password = "secreto_largo"', "Token/password generico")
        ])

    def test_modo_hook_dice_commit_bloqueado(self):
        """Sin flags (modo hook) → titulo contiene COMMIT BLOQUEADO."""
        import io
        from guardian_sombras import mostrar_resultado
        resultado = self._resultado_con_secreto()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            mostrar_resultado(resultado, es_hook=True)
        salida = mock_out.getvalue()
        self.assertIn("COMMIT BLOQUEADO", salida)
        self.assertNotIn("SECRETO DETECTADO", salida)

    def test_modo_scan_manual_dice_secreto_detectado(self):
        """--scan (modo manual) → titulo contiene SECRETO DETECTADO, no COMMIT BLOQUEADO."""
        import io
        from guardian_sombras import mostrar_resultado
        resultado = self._resultado_con_secreto()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            mostrar_resultado(resultado, es_hook=False)
        salida = mock_out.getvalue()
        self.assertIn("SECRETO DETECTADO", salida)
        self.assertNotIn("COMMIT BLOQUEADO", salida)

    def test_sin_secretos_no_imprime_nada(self):
        """Sin secretos → mostrar_resultado no imprime nada."""
        import io
        from guardian_sombras import mostrar_resultado
        resultado = ResultadoEscaneo()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            mostrar_resultado(resultado, es_hook=True)
        self.assertEqual(mock_out.getvalue(), "")

    def test_valor_por_defecto_es_manual(self):
        """Sin argumento es_hook → por defecto es False (mensaje neutro)."""
        import io
        from guardian_sombras import mostrar_resultado
        resultado = self._resultado_con_secreto()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            mostrar_resultado(resultado)
        salida = mock_out.getvalue()
        self.assertNotIn("COMMIT BLOQUEADO", salida)
        self.assertIn("SECRETO DETECTADO", salida)


if __name__ == "__main__":
    unittest.main()
