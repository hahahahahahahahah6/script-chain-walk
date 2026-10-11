"""Smoke tests for script-chain-walk (stdlib unittest only)."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from script_chain_walk.cli import main
from script_chain_walk.graph import build_graph


def write_tree(root, files):
    for rel, content in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)


class ChainWalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def test_shell_calls_shell(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\necho hi\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["from"], "a.sh")
        self.assertEqual(graph["edges"][0]["to"], "b.sh")

    def test_shell_calls_python_via_interpreter(self):
        write_tree(self.tmp, {
            "deploy.sh": "#!/bin/bash\npython3 migrate.py --all\n",
            "migrate.py": "print('migrating')\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["to"], "migrate.py")

    def test_python_calls_shell_via_subprocess(self):
        write_tree(self.tmp, {
            "run.py": "import subprocess\nsubprocess.run(['./setup.sh'])\n",
            "setup.sh": "#!/bin/bash\necho ok\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["from"], "run.py")
        self.assertEqual(graph["edges"][0]["to"], "setup.sh")

    def test_python_os_system_string(self):
        write_tree(self.tmp, {
            "run.py": "import os\nos.system('bash cleanup.sh && ./verify.sh')\n",
            "cleanup.sh": "#!/bin/bash\necho clean\n",
            "verify.sh": "#!/bin/bash\necho verify\n",
        })
        graph = build_graph(self.tmp)
        targets = sorted(e["to"] for e in graph["edges"])
        self.assertEqual(targets, ["cleanup.sh", "verify.sh"])

    def test_broken_chain_detected(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./missing.sh\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 0)
        self.assertEqual(len(graph["broken"]), 1)
        self.assertEqual(graph["broken"][0]["target"], "./missing.sh")
        self.assertEqual(graph["broken"][0]["caller"], "a.sh")

    def test_cycle_detected(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\n./a.sh\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["cycles"]), 1)
        self.assertEqual(set(graph["cycles"][0]), {"a.sh", "b.sh"})

    def test_self_cycle(self):
        write_tree(self.tmp, {
            "loop.sh": "#!/bin/bash\n./loop.sh\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["cycles"]), 1)

    def test_comments_are_ignored(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n# ./ghost.sh is not real\n./real.sh  # trailing ok\n",
            "real.sh": "#!/bin/bash\necho hi\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["to"], "real.sh")
        self.assertEqual(len(graph["broken"]), 0)

    def test_source_command(self):
        write_tree(self.tmp, {
            "main.sh": "#!/bin/bash\nsource lib.sh\n. ./helpers.sh\n",
            "lib.sh": "FOO=1\n",
            "helpers.sh": "BAR=2\n",
        })
        graph = build_graph(self.tmp)
        targets = sorted(e["to"] for e in graph["edges"])
        self.assertEqual(targets, ["helpers.sh", "lib.sh"])
        self.assertTrue(all(e["kind"] == "source" for e in graph["edges"]))

    def test_json_output_is_valid(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\necho hi\n",
        })
        code, out = self.run_cli(self.tmp, "--format", "json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("a.sh", payload["nodes"])
        self.assertEqual(payload["summary"]["edges"], 1)
        self.assertEqual(payload["entry_points"], ["a.sh"])

    def test_text_output_shows_tree(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\necho hi\n",
        })
        code, out = self.run_cli(self.tmp)
        self.assertEqual(code, 0)
        self.assertIn("a.sh", out)
        self.assertIn("b.sh", out)
        self.assertIn("scripts: 2", out)

    def test_check_flag_fails_on_broken(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./missing.sh\n",
        })
        code, _ = self.run_cli(self.tmp, "--check")
        self.assertEqual(code, 1)

    def test_check_flag_fails_on_cycle(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\n./a.sh\n",
        })
        code, _ = self.run_cli(self.tmp, "--check")
        self.assertEqual(code, 1)

    def test_check_flag_passes_on_clean(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\necho hi\n",
        })
        code, _ = self.run_cli(self.tmp, "--check")
        self.assertEqual(code, 0)

    def test_subdirectory_resolution(self):
        write_tree(self.tmp, {
            "bin/run.sh": "#!/bin/bash\n../lib/helper.sh\n",
            "lib/helper.sh": "#!/bin/bash\necho hi\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(len(graph["edges"]), 1)
        edge = graph["edges"][0]
        self.assertEqual(edge["from"], os.path.join("bin", "run.sh"))
        self.assertEqual(edge["to"], os.path.join("lib", "helper.sh"))
        self.assertEqual(edge["resolved_via"], "relative")

    def test_entry_points(self):
        write_tree(self.tmp, {
            "a.sh": "#!/bin/bash\n./b.sh\n",
            "b.sh": "#!/bin/bash\necho hi\n",
            "lonely.py": "print('alone')\n",
        })
        graph = build_graph(self.tmp)
        self.assertEqual(sorted(graph["entry_points"]), ["a.sh", "lonely.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
