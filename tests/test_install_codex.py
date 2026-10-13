"""Cross-platform installation tests; never modify the user's real skills."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parent.parent / "tools" / "install_codex.py"
SPEC = importlib.util.spec_from_file_location("install_codex", MODULE_PATH)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="academic-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "checkout" / "skills" / installer.SKILL_NAME
        self.destination = self.root / "installed" / "skills"
        fixture = {
            "SKILL.md": "---\nname: robotics-conference-video\ndescription: Build evidence-grounded videos.\n---\n",
            "manifest.yaml": "version: 1\n",
            "references/workflow.md": "Evidence → storyboard → render → QA\n",
            "scripts/render_video.py": "print('render')\n",
            "assets/storyboard.template.json": '{"segments": []}\n',
            "static/example.css": "body { color: black; }\n",
            "agents/openai.yaml": "policy:\n  allow_implicit_invocation: true\n",
        }
        for relative, content in fixture.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def test_full_tree_install_check_and_other_skill_preserved(self) -> None:
        unrelated = self.destination / "another-skill" / "SKILL.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("User content", encoding="utf-8")
        result = installer.install(self.source, self.destination)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["files"], 7)
        target = self.destination / installer.SKILL_NAME
        self.assertEqual(installer.inventory(self.source), installer.inventory(target))
        self.assertEqual(installer.install(self.source, self.destination, check=True)["status"], "PASS")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "User content")

    def test_existing_install_not_overwritten_without_update(self) -> None:
        installer.install(self.source, self.destination)
        target_file = self.destination / installer.SKILL_NAME / "SKILL.md"
        target_file.write_text("User-edited skill", encoding="utf-8")
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.destination)
        self.assertEqual(target_file.read_text(encoding="utf-8"), "User-edited skill")

    def test_check_detects_small_file_edits_and_extra_files_without_writing(self) -> None:
        installer.install(self.source, self.destination)
        target = self.destination / installer.SKILL_NAME
        (target / "assets" / "storyboard.template.json").write_text("{}\n", encoding="utf-8")
        (target / "local-notes.md").write_text("Retain these notes", encoding="utf-8")
        before = installer.inventory(target)
        result = installer.install(self.source, self.destination, check=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["differences"]["changed"], ["assets/storyboard.template.json"])
        self.assertEqual(result["differences"]["extra"], ["local-notes.md"])
        self.assertEqual(installer.inventory(target), before)

    def test_update_retains_exact_previous_tree_in_backup(self) -> None:
        installer.install(self.source, self.destination)
        target = self.destination / installer.SKILL_NAME
        (target / "local-notes.md").write_text("Keep forever", encoding="utf-8")
        before = installer.inventory(target)
        (self.source / "scripts" / "render_video.py").write_text("print('updated')\n", encoding="utf-8")
        result = installer.install(self.source, self.destination, update=True)
        self.assertEqual(result["action"], "update")
        self.assertIsNotNone(result["backup"])
        backup = Path(result["backup"])
        self.assertEqual(installer.inventory(backup), before)
        self.assertEqual(installer.inventory(target), installer.inventory(self.source))
        self.assertEqual(backup.parent.resolve(), self.destination.resolve())

    def test_update_restores_previous_tree_if_final_swap_fails(self) -> None:
        installer.install(self.source, self.destination)
        target = self.destination / installer.SKILL_NAME
        before = installer.inventory(target)
        (self.source / "manifest.yaml").write_text("version: 2\n", encoding="utf-8")
        original_replace = os.replace

        def fail_final_swap(source, destination):
            if Path(source).name.startswith(f".{installer.SKILL_NAME}.stage-"):
                raise OSError("Simulated failure during staged swap")
            return original_replace(source, destination)

        with mock.patch.object(installer.os, "replace", side_effect=fail_final_swap):
            with self.assertRaises(OSError):
                installer.install(self.source, self.destination, update=True)
        self.assertEqual(installer.inventory(target), before)
        self.assertEqual(list(self.destination.glob(f".{installer.SKILL_NAME}.stage-*")), [])

    def test_matching_update_does_not_create_unnecessary_backup(self) -> None:
        installer.install(self.source, self.destination)
        result = installer.install(self.source, self.destination, update=True)
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertIsNone(result["backup"])

    def test_transient_caches_not_copied_or_reported_as_drift(self) -> None:
        cache = self.source / "scripts" / "__pycache__" / "test.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"temporary")
        installer.install(self.source, self.destination)
        target = self.destination / installer.SKILL_NAME
        self.assertFalse((target / "scripts" / "__pycache__").exists())
        installed_cache = target / "scripts" / "__pycache__" / "local.pyc"
        installed_cache.parent.mkdir()
        installed_cache.write_bytes(b"local")
        self.assertEqual(installer.install(self.source, self.destination, check=True)["status"], "PASS")

    def test_default_path_precedence(self) -> None:
        fake_home = self.root / "home"
        custom = self.root / "custom-codex"
        self.assertEqual(installer.default_destination({}, fake_home), fake_home / ".codex" / "skills")
        agents = fake_home / ".agents" / "skills"
        agents.mkdir(parents=True)
        self.assertEqual(installer.default_destination({}, fake_home), agents)
        self.assertEqual(installer.default_destination({"CODEX_HOME": str(custom)}, fake_home), custom / "skills")

    def test_nested_source_and_target_are_refused(self) -> None:
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.source.parent)
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.source / "nested")

    def test_symlink_source_and_target_refused(self) -> None:
        link = self.root / "linked-source"

        def create_link(path: Path) -> None:
            try:
                path.symlink_to(self.source, target_is_directory=True)
            except OSError:
                if os.name != "nt":
                    raise
                # Windows junctions exercise the reparse-point guard without
                # requiring the Create Symbolic Links privilege.
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(self.source)],
                                        capture_output=True, text=True, check=False)
                if result.returncode:
                    raise OSError("Unable to create an isolated Windows junction")

        try:
            create_link(link)
        except OSError:
            self.skipTest("Creating symlinks or directory junctions is unavailable on this host")
        with self.assertRaises(installer.InstallError):
            installer.install(link, self.destination)
        self.destination.mkdir(parents=True)
        target = self.destination / installer.SKILL_NAME
        create_link(target)
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.destination, update=True)
        self.assertTrue(installer.is_link(target))
        self.assertTrue((self.source / "SKILL.md").is_file())

    def test_install_does_not_replace_a_tree_that_appears_during_staging(self) -> None:
        original_copy = installer.shutil.copy2
        target = self.destination / installer.SKILL_NAME
        created = False

        def create_user_tree_then_copy(source, destination):
            nonlocal created
            if not created:
                created = True
                target.mkdir(parents=True)
                (target / "local-notes.md").write_text("Concurrent user file", encoding="utf-8")
            return original_copy(source, destination)

        with mock.patch.object(installer.shutil, "copy2", side_effect=create_user_tree_then_copy):
            with self.assertRaises(installer.InstallError):
                installer.install(self.source, self.destination)
        self.assertEqual((target / "local-notes.md").read_text(encoding="utf-8"), "Concurrent user file")
        self.assertFalse((target / "SKILL.md").exists())

    def test_check_missing_install_has_no_side_effects(self) -> None:
        with self.assertRaises(installer.InstallError):
            installer.install(self.source, self.destination, check=True)
        self.assertFalse(self.destination.exists())

    def test_real_checkout_all_maintained_files_install(self) -> None:
        self.assertTrue((installer.SOURCE / "SKILL.md").is_file(), "Release must include the skill before testing")
        destination = self.root / "release-install" / "skills"
        result = installer.install(installer.SOURCE, destination)
        self.assertGreaterEqual(result["files"], 20)
        self.assertEqual(installer.install(installer.SOURCE, destination, check=True)["status"], "PASS")
        target = destination / installer.SKILL_NAME
        for expected in ["manifest.yaml", "references/evidence-patterns.md", "scripts/render_video.py", "scripts/test_academic_video.py", "assets/storyboard.template.json", "agents/openai.yaml"]:
            self.assertTrue((target / expected).is_file(), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
