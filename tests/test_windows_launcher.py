import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows launcher test")
class WindowsLauncherTests(unittest.TestCase):
    def test_batch_file_is_ascii_with_windows_line_endings(self) -> None:
        content = (PROJECT_ROOT / "start.cmd").read_bytes()

        self.assertTrue(content.isascii())
        self.assertIn(b"\r\n", content)
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))

    def test_batch_launcher_runs_unicode_safe_powershell_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "course"
            source.mkdir()
            # Explicit .\ so cmd finds the launcher even when NoDefaultCurrentDirectoryInExePath is set.
            command = f"call .\\start.cmd -Source {source} -Check"
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", command],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Launcher check passed.", result.stdout)

    def test_powershell_launcher_is_utf8_with_bom(self) -> None:
        content = (PROJECT_ROOT / "scripts" / "start-workbench.ps1").read_bytes()

        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
