#!/usr/bin/env python3
"""Tests para Cronista de Cambios."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cronista_cambios import (
    Commit,
    clasificar_conventional,
    clasificar_por_keywords,
    clasificar_commit,
    es_ruido,
    parsear_commit,
    procesar_commits,
    agrupar_por_categoria,
    generar_markdown,
    generar_notas,
    obtener_ultimo_tag,
    resolver_rango,
    escribir_changelog,
    CATEGORIAS,
)


class TestClasificacionConventional(unittest.TestCase):
    """Tests para parseo de Conventional Commits."""

    def test_feat_simple(self):
        resultado = clasificar_conventional("feat: añadir soporte multi-perfil")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "feat")
        self.assertEqual(resultado[1], "añadir soporte multi-perfil")

    def test_fix_con_scope(self):
        resultado = clasificar_conventional("fix(auth): corregir validacion de token")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "fix")
        self.assertEqual(resultado[1], "corregir validacion de token")

    def test_refactor(self):
        resultado = clasificar_conventional("refactor: limpiar modulo de config")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "refactor")

    def test_docs(self):
        resultado = clasificar_conventional("docs: actualizar README")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "docs")

    def test_breaking_change(self):
        resultado = clasificar_conventional("feat!: cambiar API de autenticacion")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "feat")

    def test_tipo_desconocido_con_alias(self):
        resultado = clasificar_conventional("build: actualizar Makefile")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "chore")

    def test_no_conventional(self):
        resultado = clasificar_conventional("Actualizar el README con instrucciones")
        self.assertIsNone(resultado)

    def test_chore(self):
        resultado = clasificar_conventional("chore: actualizar dependencias")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "chore")

    def test_test(self):
        resultado = clasificar_conventional("test: añadir tests de integracion")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "test")

    def test_perf(self):
        resultado = clasificar_conventional("perf: optimizar consultas SQL")
        self.assertIsNotNone(resultado)
        self.assertEqual(resultado[0], "perf")


class TestClasificacionKeywords(unittest.TestCase):
    """Tests para clasificacion por keywords."""

    def test_add_como_feat(self):
        self.assertEqual(clasificar_por_keywords("Add new dashboard view"), "feat")

    def test_new_como_feat(self):
        self.assertEqual(clasificar_por_keywords("New login page"), "feat")

    def test_fix_como_fix(self):
        self.assertEqual(clasificar_por_keywords("Fix crash on startup"), "fix")

    def test_bug_como_fix(self):
        self.assertEqual(clasificar_por_keywords("Bug in form validation"), "fix")

    def test_refactor_como_refactor(self):
        self.assertEqual(clasificar_por_keywords("Refactor config module"), "refactor")

    def test_clean_como_refactor(self):
        self.assertEqual(clasificar_por_keywords("Clean up unused imports"), "refactor")

    def test_doc_como_docs(self):
        self.assertEqual(clasificar_por_keywords("Update documentation"), "docs")

    def test_test_como_test(self):
        self.assertEqual(clasificar_por_keywords("Run test coverage for auth"), "test")

    def test_keyword_español_añadir(self):
        self.assertEqual(clasificar_por_keywords("Añadir soporte para perfiles"), "feat")

    def test_keyword_español_corregir(self):
        self.assertEqual(clasificar_por_keywords("Corregir error en formulario"), "fix")

    def test_sin_keyword_other(self):
        self.assertEqual(clasificar_por_keywords("Miscellaneous changes"), "other")


class TestFiltroRuido(unittest.TestCase):
    """Tests para filtrado de commits de ruido."""

    def test_merge_commit(self):
        self.assertTrue(es_ruido("Merge branch 'develop' into main"))

    def test_merge_pull_request(self):
        self.assertTrue(es_ruido("Merge pull request #42 from user/branch"))

    def test_wip_mayusculas(self):
        self.assertTrue(es_ruido("WIP: trabajando en algo"))

    def test_wip_minusculas(self):
        self.assertTrue(es_ruido("wip cambios parciales"))

    def test_initial_commit(self):
        self.assertTrue(es_ruido("Initial commit"))

    def test_bump_version(self):
        self.assertTrue(es_ruido("Bump version to 1.2.3"))

    def test_auto_generated(self):
        self.assertTrue(es_ruido("Auto-generated release notes"))

    def test_solo_coauthored(self):
        self.assertTrue(es_ruido("Co-Authored-By: User <user@mail.com>"))

    def test_commit_normal_no_es_ruido(self):
        self.assertFalse(es_ruido("feat: añadir nueva funcionalidad"))

    def test_commit_con_fix_no_es_ruido(self):
        self.assertFalse(es_ruido("fix: corregir error en login"))


class TestParseoCommit(unittest.TestCase):
    """Tests para parseo de lineas raw de git log."""

    def test_parseo_correcto(self):
        linea = "abc1234def|feat: nueva funcionalidad|Juan|2026-05-27 10:30:00 +0200"
        commit = parsear_commit(linea)
        self.assertIsNotNone(commit)
        self.assertEqual(commit.hash, "abc1234def")
        self.assertEqual(commit.mensaje, "feat: nueva funcionalidad")
        self.assertEqual(commit.autor, "Juan")
        self.assertIn("2026-05-27", commit.fecha)

    def test_linea_invalida(self):
        commit = parsear_commit("esto no tiene formato")
        self.assertIsNone(commit)

    def test_mensaje_con_pipe(self):
        linea = "abc1234|fix: arreglar tema|Pedro|2026-05-27 10:30:00 +0200"
        commit = parsear_commit(linea)
        self.assertIsNotNone(commit)
        self.assertEqual(commit.mensaje, "fix: arreglar tema")


class TestProcesamientoCompleto(unittest.TestCase):
    """Tests para el pipeline completo de procesamiento."""

    def test_procesar_commits_filtra_ruido(self):
        lineas = [
            "aaa|feat: nueva feature|Ana|2026-05-27 10:00:00 +0200",
            "bbb|Merge branch 'dev' into main|Bot|2026-05-27 09:00:00 +0200",
            "ccc|fix: corregir bug|Carlos|2026-05-27 08:00:00 +0200",
            "ddd|WIP: en progreso|Ana|2026-05-27 07:00:00 +0200",
        ]
        commits = procesar_commits(lineas)
        self.assertEqual(len(commits), 2)
        categorias = [c.categoria for c in commits]
        self.assertIn("feat", categorias)
        self.assertIn("fix", categorias)

    def test_procesar_commits_clasifica_conventional(self):
        lineas = [
            "aaa|docs: actualizar README|Ana|2026-05-27 10:00:00 +0200",
        ]
        commits = procesar_commits(lineas)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].categoria, "docs")
        self.assertEqual(commits[0].descripcion, "actualizar README")

    def test_procesar_commits_clasifica_por_keywords(self):
        lineas = [
            "aaa|Add new dashboard|Ana|2026-05-27 10:00:00 +0200",
        ]
        commits = procesar_commits(lineas)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0].categoria, "feat")


class TestAgrupacion(unittest.TestCase):
    """Tests para agrupacion por categoria."""

    def test_agrupa_correctamente(self):
        c1 = Commit("aaa", "feat: algo", "Ana", "2026-05-27")
        c1.categoria = "feat"
        c2 = Commit("bbb", "fix: otro", "Bob", "2026-05-27")
        c2.categoria = "fix"
        c3 = Commit("ccc", "feat: mas", "Ana", "2026-05-27")
        c3.categoria = "feat"

        grupos = agrupar_por_categoria([c1, c2, c3])

        self.assertIn("feat", grupos)
        self.assertIn("fix", grupos)
        self.assertEqual(len(grupos["feat"]), 2)
        self.assertEqual(len(grupos["fix"]), 1)

    def test_orden_categorias(self):
        c1 = Commit("aaa", "fix: algo", "Ana", "2026-05-27")
        c1.categoria = "fix"
        c2 = Commit("bbb", "feat: otro", "Bob", "2026-05-27")
        c2.categoria = "feat"

        grupos = agrupar_por_categoria([c1, c2])
        claves = list(grupos.keys())
        # feat debe venir antes que fix segun CATEGORIAS
        self.assertEqual(claves[0], "feat")
        self.assertEqual(claves[1], "fix")


class TestGeneracionMarkdown(unittest.TestCase):
    """Tests para generacion de changelog markdown."""

    def _crear_grupos(self):
        c1 = Commit("aaa1234", "feat: nueva feature", "Ana", "2026-05-27")
        c1.categoria = "feat"
        c1.descripcion = "nueva feature"
        c2 = Commit("bbb5678", "fix: corregir bug", "Bob", "2026-05-27")
        c2.categoria = "fix"
        c2.descripcion = "corregir bug"
        return agrupar_por_categoria([c1, c2])

    def test_markdown_basico(self):
        grupos = self._crear_grupos()
        md = generar_markdown(grupos, version="v1.0.0", fecha="2026-05-27")

        self.assertIn("## [v1.0.0] - 2026-05-27", md)
        self.assertIn("### Nuevas funcionalidades", md)
        self.assertIn("- nueva feature", md)
        self.assertIn("### Correcciones", md)
        self.assertIn("- corregir bug", md)

    def test_markdown_con_hash(self):
        grupos = self._crear_grupos()
        md = generar_markdown(grupos, version="v1.0.0", fecha="2026-05-27",
                              incluir_hash=True)
        self.assertIn("`aaa1234`", md)

    def test_markdown_con_autor(self):
        grupos = self._crear_grupos()
        md = generar_markdown(grupos, version="v1.0.0", fecha="2026-05-27",
                              incluir_autor=True)
        self.assertIn("Ana", md)

    def test_markdown_sin_version(self):
        grupos = self._crear_grupos()
        md = generar_markdown(grupos, fecha="2026-05-27")
        self.assertIn("[Sin version]", md)


class TestGeneracionNotas(unittest.TestCase):
    """Tests para formato de release notes."""

    def test_notas_formato_plano(self):
        c1 = Commit("aaa", "feat: algo", "Ana", "2026-05-27")
        c1.categoria = "feat"
        c1.descripcion = "algo genial"
        grupos = agrupar_por_categoria([c1])

        notas = generar_notas(grupos)
        self.assertIn("Nuevas funcionalidades:", notas)
        self.assertIn("  - algo genial", notas)
        # Sin headers markdown
        self.assertNotIn("##", notas)
        self.assertNotIn("###", notas)


class TestObtenerUltimoTag(unittest.TestCase):
    """Tests para deteccion de ultimo tag."""

    @patch("cronista_cambios.subprocess.run")
    def test_con_tag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="v1.2.3\n")
        tag = obtener_ultimo_tag()
        self.assertEqual(tag, "v1.2.3")

    @patch("cronista_cambios.subprocess.run")
    def test_sin_tags(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal")
        tag = obtener_ultimo_tag()
        self.assertIsNone(tag)


class TestResolverRango(unittest.TestCase):
    """Tests para resolucion de rangos."""

    def test_rango_explicito(self):
        rango, version = resolver_rango("v1.0.0..v1.1.0", None)
        self.assertEqual(rango, "v1.0.0..v1.1.0")
        self.assertEqual(version, "v1.1.0")

    def test_rango_hasta_head(self):
        rango, version = resolver_rango("v1.0.0..HEAD", None)
        self.assertEqual(rango, "v1.0.0..HEAD")
        self.assertIsNone(version)

    def test_desde_ref(self):
        rango, version = resolver_rango(None, "abc123")
        self.assertEqual(rango, "abc123..HEAD")
        self.assertIsNone(version)

    @patch("cronista_cambios.obtener_ultimo_tag", return_value="v2.0.0")
    def test_default_con_tag(self, mock_tag):
        rango, version = resolver_rango(None, None)
        self.assertEqual(rango, "v2.0.0..HEAD")
        self.assertIsNone(version)

    @patch("cronista_cambios.obtener_ultimo_tag", return_value=None)
    def test_default_sin_tags(self, mock_tag):
        rango, version = resolver_rango(None, None)
        self.assertIsNone(rango)
        self.assertIsNone(version)


class TestEscribirChangelog(unittest.TestCase):
    """Tests para escritura de archivo CHANGELOG.md."""

    def test_crear_nuevo(self):
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        ruta = tmpdir / "CHANGELOG.md"
        contenido = "## [v1.0.0] - 2026-05-27\n\n### Nuevas funcionalidades\n- algo\n"

        escribir_changelog(ruta, contenido)

        self.assertTrue(ruta.exists())
        texto = ruta.read_text(encoding="utf-8")
        self.assertIn("# Changelog", texto)
        self.assertIn("## [v1.0.0]", texto)

        # Limpiar
        ruta.unlink()
        tmpdir.rmdir()

    def test_actualizar_existente(self):
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        ruta = tmpdir / "CHANGELOG.md"
        ruta.write_text(
            "# Changelog\n\n## [v1.0.0] - 2026-05-20\n\n### Correcciones\n- viejo fix\n",
            encoding="utf-8",
        )

        contenido_nuevo = "## [v1.1.0] - 2026-05-27\n\n### Nuevas funcionalidades\n- algo nuevo\n"
        escribir_changelog(ruta, contenido_nuevo)

        texto = ruta.read_text(encoding="utf-8")
        self.assertIn("## [v1.1.0]", texto)
        self.assertIn("## [v1.0.0]", texto)
        # v1.1.0 debe aparecer antes que v1.0.0
        pos_nuevo = texto.index("[v1.1.0]")
        pos_viejo = texto.index("[v1.0.0]")
        self.assertLess(pos_nuevo, pos_viejo)

        # Limpiar
        ruta.unlink()
        tmpdir.rmdir()


class TestObtenerCommitsRawTupla(unittest.TestCase):
    """R4-008: obtener_commits_raw devuelve (bool, list) distinguiendo fallo vs vacío."""

    @patch("cronista_cambios.subprocess.run")
    def test_git_falla_devuelve_false_lista_vacia(self, mock_run):
        """Si git retorna código≠0 (ref/fecha inválida), devuelve (False, [])."""
        from cronista_cambios import obtener_commits_raw
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: bad ref")
        ok, lineas = obtener_commits_raw("fecha_invalida_xyz..HEAD")
        self.assertFalse(ok)
        self.assertEqual(lineas, [])

    @patch("cronista_cambios.subprocess.run")
    def test_git_ok_vacio_devuelve_true_lista_vacia(self, mock_run):
        """Si git retorna 0 pero sin commits (rango válido vacío), devuelve (True, [])."""
        from cronista_cambios import obtener_commits_raw
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, lineas = obtener_commits_raw("v9.9.9..HEAD")
        self.assertTrue(ok)
        self.assertEqual(lineas, [])

    @patch("cronista_cambios.subprocess.run")
    def test_git_ok_con_commits_devuelve_true_lineas(self, mock_run):
        """Si git retorna 0 con commits, devuelve (True, [lineas...])."""
        from cronista_cambios import obtener_commits_raw
        linea = "abc123|feat: algo|Autor|2026-01-01 10:00:00 +0100"
        mock_run.return_value = MagicMock(returncode=0, stdout=linea, stderr="")
        ok, lineas = obtener_commits_raw(None)
        self.assertTrue(ok)
        self.assertEqual(lineas, [linea])


class TestMainDistingueInvalidaVsVacia(unittest.TestCase):
    """R4-008: main() distingue ref/fecha inválida (exit 1) de rango vacío (exit 0)."""

    def _run_main_mocked(self, argv, git_ok, git_lineas):
        """Ejecuta main() con git mockeado y devuelve el código de salida."""
        import cronista_cambios as mod
        with patch("sys.argv", argv):
            with patch.object(mod, "verificar_repo_git", return_value=True):
                with patch.object(mod, "cargar_config", return_value={}):
                    with patch.object(
                        mod, "obtener_commits_raw", return_value=(git_ok, git_lineas)
                    ):
                        with patch.object(mod, "notificar"):
                            return mod.main()

    def test_fecha_invalida_sale_1(self):
        """git falla → main devuelve 1."""
        codigo = self._run_main_mocked(
            ["changelog", "--desde", "fecha_invalida"],
            git_ok=False,
            git_lineas=[],
        )
        self.assertEqual(codigo, 1)

    def test_rango_valido_sin_commits_sale_0(self):
        """git ok pero vacío → main devuelve 0."""
        codigo = self._run_main_mocked(
            ["changelog", "--desde", "2020-01-01"],
            git_ok=True,
            git_lineas=[],
        )
        self.assertEqual(codigo, 0)


if __name__ == "__main__":
    unittest.main()
