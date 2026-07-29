#!/usr/bin/env python3
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from actualizador.actualizador import (
    hay_conexion,
    pacman_bloqueado,
    guardar_snapshot,
    listar_actualizaciones_pacman,
    listar_actualizaciones_aur,
    actualizar_pacman,
    actualizar_aur,
    main as actualizador_main,
)


def _mock_result(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class TestPacmanBloqueado(TestCase):
    def test_no_bloqueado(self):
        with patch("actualizador.actualizador.DB_LOCK", Path("/tmp/no_existe_lock_test")):
            self.assertFalse(pacman_bloqueado())

    def test_bloqueado(self):
        with tempfile.NamedTemporaryFile() as f:
            with patch("actualizador.actualizador.DB_LOCK", Path(f.name)):
                self.assertTrue(pacman_bloqueado())


class TestGuardarSnapshot(TestCase):
    def test_guarda_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("actualizador.actualizador.RUTA_SNAPSHOTS", Path(tmp)):
                guardar_snapshot(
                    ["firefox 130.0-1 -> 131.0-1"],
                    ["yay-bin 12.3-1 -> 12.4-1"],
                )
                archivos = list(Path(tmp).glob("*.txt"))
                self.assertEqual(len(archivos), 1)
                contenido = archivos[0].read_text()
                self.assertIn("PACMAN", contenido)
                self.assertIn("firefox", contenido)
                self.assertIn("AUR", contenido)
                self.assertIn("yay-bin", contenido)

    def test_snapshot_solo_pacman(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("actualizador.actualizador.RUTA_SNAPSHOTS", Path(tmp)):
                guardar_snapshot(["vlc 3.0.20-1 -> 3.0.21-1"], [])
                contenido = list(Path(tmp).glob("*.txt"))[0].read_text()
                self.assertIn("PACMAN", contenido)
                self.assertNotIn("AUR", contenido)


class TestHayConexion(TestCase):
    @patch("actualizador.actualizador.subprocess.run")
    def test_con_conexion(self, mock_run):
        mock_run.return_value = _mock_result(0)
        self.assertTrue(hay_conexion())

    @patch("actualizador.actualizador.subprocess.run")
    def test_sin_conexion(self, mock_run):
        mock_run.return_value = _mock_result(1)
        self.assertFalse(hay_conexion())


class TestListarActualizaciones(TestCase):
    @patch("actualizador.actualizador.subprocess.run")
    def test_pacman_con_actualizaciones(self, mock_run):
        mock_run.return_value = _mock_result(0, "firefox 130.0-1 -> 131.0-1\nvlc 3.0.20-1 -> 3.0.21-1\n")
        paquetes = listar_actualizaciones_pacman()
        self.assertEqual(len(paquetes), 2)

    @patch("actualizador.actualizador.subprocess.run")
    def test_pacman_sin_actualizaciones(self, mock_run):
        mock_run.return_value = _mock_result(2, "")
        paquetes = listar_actualizaciones_pacman()
        self.assertEqual(paquetes, [])

    @patch("actualizador.actualizador.subprocess.run")
    def test_aur_con_actualizaciones(self, mock_run):
        mock_run.return_value = _mock_result(0, "yay-bin 12.3-1 -> 12.4-1\n")
        paquetes = listar_actualizaciones_aur()
        self.assertEqual(len(paquetes), 1)

    @patch("actualizador.actualizador.subprocess.run")
    def test_aur_sin_actualizaciones(self, mock_run):
        mock_run.return_value = _mock_result(0, "")
        paquetes = listar_actualizaciones_aur()
        self.assertEqual(paquetes, [])


class TestActualizarDryRun(TestCase):
    @patch("actualizador.actualizador.listar_actualizaciones_pacman")
    def test_pacman_dry_run(self, mock_listar):
        mock_listar.return_value = ["firefox 130->131", "vlc 3.0.20->3.0.21"]
        ok, n = actualizar_pacman(dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(n, 2)

    @patch("actualizador.actualizador.listar_actualizaciones_aur")
    def test_aur_dry_run(self, mock_listar):
        mock_listar.return_value = ["yay-bin 12.3->12.4"]
        ok, n = actualizar_aur(dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(n, 1)


class TestActualizarReal(TestCase):
    @patch("actualizador.actualizador.subprocess.run")
    def test_pacman_ok(self, mock_run):
        mock_run.return_value = _mock_result(0, "upgrading firefox\nupgrading vlc\n")
        ok, n = actualizar_pacman(dry_run=False)
        self.assertTrue(ok)
        self.assertEqual(n, 2)

    @patch("actualizador.actualizador.subprocess.run")
    def test_pacman_falla(self, mock_run):
        mock_run.return_value = _mock_result(1, "", "error: failed to commit transaction")
        ok, n = actualizar_pacman(dry_run=False)
        self.assertFalse(ok)
        self.assertEqual(n, 0)

    @patch("actualizador.actualizador.subprocess.run")
    def test_aur_ok(self, mock_run):
        mock_run.return_value = _mock_result(0, "upgrading yay-bin\n")
        ok, n = actualizar_aur(dry_run=False)
        self.assertTrue(ok)
        self.assertEqual(n, 1)


class TestMainFlagApply(TestCase):
    """N-001: sin flags no debe invocar pacman; con --apply sí entra en la rama real."""

    @patch("actualizador.actualizador.hay_conexion", return_value=False)
    def test_sin_flags_muestra_ayuda_y_sale_0(self, _mock_conn):
        """Sin flags: imprime ayuda y termina con exit 0 sin tocar el sistema."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["actualizador"]):
                actualizador_main()
        self.assertEqual(ctx.exception.code, 0)

    @patch("actualizador.actualizador.hay_conexion", return_value=False)
    def test_sin_flags_no_invoca_subprocess(self, _mock_conn):
        """Sin flags: subprocess.run nunca se invoca (no hay llamadas a pacman)."""
        with patch("actualizador.actualizador.subprocess.run") as mock_run:
            with self.assertRaises(SystemExit):
                with patch("sys.argv", ["actualizador"]):
                    actualizador_main()
            # subprocess.run no debe haber sido llamado para pacman/yay
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call[1].get("args", [])
                if isinstance(args, list):
                    self.assertNotIn("pacman", args)
                    self.assertNotIn("yay", args)

    @patch("actualizador.actualizador.guardar_snapshot")
    @patch("actualizador.actualizador.listar_actualizaciones_aur", return_value=[])
    @patch("actualizador.actualizador.listar_actualizaciones_pacman", return_value=[])
    @patch("actualizador.actualizador.pacman_bloqueado", return_value=False)
    @patch("actualizador.actualizador.hay_conexion", return_value=True)
    def test_apply_entra_en_rama_ejecucion(self, _conn, _bloq, mock_pac, mock_aur, mock_snap):
        """Con --apply la rama de ejecución real se activa (listar actualizaciones se llama)."""
        with patch("sys.argv", ["actualizador", "--apply"]):
            actualizador_main()
        mock_pac.assert_called_once()

    @patch("actualizador.actualizador.hay_conexion", return_value=False)
    def test_dry_run_sin_apply_funciona(self, _conn):
        """--dry-run sigue funcionando sin necesitar --apply."""
        with patch("sys.argv", ["actualizador", "--dry-run"]):
            actualizador_main()  # No debe lanzar excepción

    @patch("actualizador.actualizador.hay_conexion", return_value=False)
    def test_check_sin_apply_funciona(self, _conn):
        """--check sigue funcionando sin necesitar --apply."""
        with patch("sys.argv", ["actualizador", "--check"]):
            actualizador_main()  # No debe lanzar excepción


class TestFlagsMutuamenteExcluyentes(TestCase):
    """R3-002: --dry-run, --check y --apply son mutuamente excluyentes."""

    def test_dry_run_y_check_juntos_dan_exit_2(self):
        """--dry-run --check juntos → argparse.error → SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["actualizador", "--dry-run", "--check"]):
                actualizador_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_dry_run_y_apply_juntos_dan_exit_2(self):
        """--dry-run --apply juntos → SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["actualizador", "--dry-run", "--apply"]):
                actualizador_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_check_y_apply_juntos_dan_exit_2(self):
        """--check --apply juntos → SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["actualizador", "--check", "--apply"]):
                actualizador_main()
        self.assertEqual(ctx.exception.code, 2)

    def test_los_tres_juntos_dan_exit_2(self):
        """--dry-run --check --apply juntos → SystemExit(2)."""
        with self.assertRaises(SystemExit) as ctx:
            with patch("sys.argv", ["actualizador", "--dry-run", "--check", "--apply"]):
                actualizador_main()
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    main()
