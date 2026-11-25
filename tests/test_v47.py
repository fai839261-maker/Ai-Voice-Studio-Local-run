"""Portable update path checks. PowerShell/Inno behavior requires Windows tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.update_local import upgrade_arguments
from studio.core import APP_VERSION

PROJECT=Path(__file__).resolve().parents[1]

class AutomaticUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.app=self.base/'custom install'/'AI Voice Studio'
        self.app.mkdir(parents=True)
        self.exe=self.app/'AI_Voice_Studio.exe'
        self.exe.write_bytes(b'MZ example')
        (self.app/'unins000.exe').write_bytes(b'MZ example')
    def tearDown(self):self.tmp.cleanup()
    def test_passes_current_custom_installation_as_one_argument(self):
        with patch('studio.migration.stable_app',return_value=self.app):
            args=upgrade_arguments('update.exe',exe=self.exe)
        self.assertEqual(args,['update.exe','/AVSOFFLINE=1','/NORESTART','/SILENT','/DIR='+str(self.app)])
    def test_preserves_stable_path_after_move(self):
        logical=self.base/'old installation'
        logical.symlink_to(self.app,target_is_directory=True)
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.base)}), patch('studio.migration.read_json',return_value={'app':str(logical)}):
            args=upgrade_arguments('update.exe',exe=self.exe)
        self.assertEqual(args[-1],'/DIR='+str(logical))
    def test_no_guess_if_installation_missing(self):
        (self.app/'unins000.exe').unlink()
        with patch('studio.migration.stable_app',return_value=self.app):
            with self.assertRaisesRegex(RuntimeError,'verify the installed'):upgrade_arguments('update.exe',exe=self.exe)
    def test_source_update_no_optional_folder_picker(self):
        src=(PROJECT/'studio/ui_v3.py').read_text().split('    def confirm_source_update',1)[1].split('    def confirm_local_update',1)[0]
        self.assertNotIn('getExistingDirectory',src)
        self.assertIn('build_source(package)',src)
    def test_cache_and_invalid_environment_fallback_contract(self):
        src=(PROJECT/'Build-Windows.ps1').read_text()
        self.assertIn("Join-Path $data 'build-tools'",src)
        self.assertIn('Test-BuildEnvironment $candidate',src)
        self.assertIn('$cachedAlternates',src)
        self.assertIn('[IO.FileShare]::None',src)
        self.assertNotIn('The selected existing build environment is invalid',src)
    def test_hidden_main_launcher_and_child_commands_contract(self):
        build=(PROJECT/'Build-Windows.ps1').read_text()
        launcher=(PROJECT/'UPDATE_MY_SOFTWARE.vbs').read_text()
        upgrade=(PROJECT/'Upgrade-Installed.ps1').read_text()
        for marker in ['CreateNoWindow = $true','UseShellExecute = $false','ReadToEndAsync()','Invoke-HiddenNative $iscc']:
            self.assertIn(marker,build)
        self.assertIn('shell.Run command, 0, False',launcher)
        self.assertIn('-WindowStyle Hidden',upgrade)
        self.assertNotIn('FolderBrowserDialog',upgrade)
    def test_standalone_and_direct_installation_detection_contract(self):
        src=(PROJECT/'Upgrade-Installed.ps1').read_text()
        iss=(PROJECT/'installer/AI_Voice_Studio.iss').read_text()
        for marker in ['Registry64','Registry32','InstallLocation','unins*.exe','/DIR=','Installation location changed during the build']:
            self.assertIn(marker,src)
        self.assertIn('UsePreviousAppDir=yes',iss)
        self.assertIn('DisableDirPage=auto',iss)
        self.assertIn('AI_Voice_Studio_Setup_'+APP_VERSION,iss)
