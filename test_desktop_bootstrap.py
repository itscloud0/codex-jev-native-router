import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import desktop_bootstrap


class DesktopBootstrapTests(unittest.TestCase):
    def test_signed_slot_execs_native_and_sidecar_preserves_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps({"mode": "off"}))
            native = root / "native"
            native.write_text("#!/usr/bin/env python3\n"
                              "import json, os, sys\n"
                              "request = json.loads(sys.stdin.readline())\n"
                              "print(json.dumps({'pid': os.getpid(), 'ppid': os.getppid(), 'request': request}), flush=True)\n")
            native.chmod(0o700)
            proc = subprocess.Popen([sys.executable, str(Path(desktop_bootstrap.__file__)),
                                     "--native", str(native), "--root", str(root), "--", "app-server"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output, error = proc.communicate(b'{"id":1,"method":"initialize"}\n', timeout=5)
            self.assertEqual(proc.returncode, 0, error.decode())
            result = json.loads(output)
            self.assertEqual(result["pid"], proc.pid)
            self.assertEqual(result["ppid"], os.getpid())
            self.assertEqual(result["request"], {"id": 1, "method": "initialize"})

    def test_missing_native_reports_path_and_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            result = subprocess.run([sys.executable, str(Path(desktop_bootstrap.__file__)),
                                     "--native", str(missing), "--root", directory, "--", "app-server"],
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 127)
            self.assertIn(b"Effortlane Desktop", result.stderr)
            self.assertIn(b"native Codex missing or not executable", result.stderr)
            self.assertNotIn(b"Jev", result.stderr)


if __name__ == "__main__":
    unittest.main()
