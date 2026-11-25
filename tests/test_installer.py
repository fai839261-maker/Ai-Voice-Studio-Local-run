import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import managed
from studio.preflight import prepare_runtime
from studio.core import APP_VERSION

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'AI_VOICE_STUDIO_HOME': self.tmp.name})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def test_reuse_runtime(self):
        with patch.object(managed, 'ensure_runtime_serial', return_value=Path('existing-python.exe')) as ensure, patch.object(managed, 'import_ok', return_value=True), patch.object(managed, 'run') as run:
            self.assertEqual(prepare_runtime(lambda:0),0)
            ensure.assert_called_once()
            self.assertEqual(run.call_count,1)
            self.assertIn('--version', run.call_args.args[0])
    def test_failed_resources_do_not_install_runtime(self):
        with patch.object(managed, 'ensure_runtime_serial') as ensure:
            self.assertEqual(prepare_runtime(lambda:3),4)
            ensure.assert_not_called()
            self.assertIn('ERROR', (Path(self.tmp.name)/'logs/setup-preflight.log').read_text())
    def test_runtime_install_failure_reported(self):
        with patch.object(managed, 'ensure_runtime_serial', side_effect=RuntimeError('WinGet missing')):
            self.assertEqual(prepare_runtime(lambda:0),4)
            self.assertIn('WinGet missing', (Path(self.tmp.name)/'logs/setup-preflight.log').read_text())
    def test_configuration_regression(self):
        root=Path(__file__).resolve().parents[1]
        iss=(root/'installer/AI_Voice_Studio.iss').read_text()
        build=(root/'Build-Windows.ps1').read_text()
        self.assertIn('DisableDirPage=auto',iss)
        self.assertIn('PrivilegesRequired=lowest',iss)
        self.assertIn('Flags: shellexec nowait postinstall skipifsilent runasoriginaluser',iss)
        self.assertIn('--prepare-runtime',iss)
        self.assertNotIn('--uac-admin',build)
        self.assertIn('AI_Voice_Studio_Setup_'+APP_VERSION,iss)
        self.assertIn('AI_Voice_Studio_Setup_'+APP_VERSION,build)
