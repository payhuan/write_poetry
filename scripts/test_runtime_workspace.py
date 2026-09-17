import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import runtime_workspace as workspace


class RuntimeWorkspaceTests(unittest.TestCase):
    def tearDown(self):
        workspace.configure_workspace(None)

    def test_relative_paths_use_external_workspace_and_static_inputs_fallback(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {workspace.ENV_NAME: temporary}):
            workspace.configure_workspace(None)
            root = Path(temporary).resolve()
            self.assertEqual(workspace.output_path('runs/a.json'), root / 'runs' / 'a.json')
            self.assertEqual(workspace.input_path('data/rhyme/source.json'),
                             workspace.PROJECT / 'data' / 'rhyme' / 'source.json')
            with self.assertRaises(ValueError):
                workspace.output_path(workspace.PROJECT / 'runs' / 'a.json')

    def test_cli_writes_to_workspace_without_changing_skill_tree(self):
        project = Path(__file__).resolve().parent.parent
        before = {p.relative_to(project).as_posix() for p in project.rglob('*')
                  if '__pycache__' not in p.parts}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / 'request.json'
            request.write_text(json.dumps({'raw_request': '咏梅', 'theme': '梅'},
                                          ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([
                sys.executable, str(project / 'scripts' / 'init_state.py'),
                '--input', str(request), '--output', 'states/poem.json',
                '--work-dir', str(root / 'work'),
            ], cwd=project, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / 'work' / 'states' / 'poem.json').is_file())
        after = {p.relative_to(project).as_posix() for p in project.rglob('*')
                 if '__pycache__' not in p.parts}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
