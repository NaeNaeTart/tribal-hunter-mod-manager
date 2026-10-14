"""Manager setup tests: app installation must not deploy or alter mods."""
import tempfile,unittest,json
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mod-manager'))
from setup import install

class SetupTests(unittest.TestCase):
 def setUp(self):
  self.work=tempfile.TemporaryDirectory();self.addCleanup(self.work.cleanup)
  self.base=Path(self.work.name);self.root=self.base/'game';self.root.mkdir()
  (self.root/'Tribal Hunter.exe').write_bytes(b'game exe')
  (self.root/'data.win').write_bytes(b'currently modded game')
  self.payload=self.base/'manager.exe';self.payload.write_bytes(b'new manager')
  self.tools=self.base/'compiler';self.tools.mkdir()
  (self.tools/'UndertaleModCli.exe').write_bytes(b'compiler')
  self.registration=patch('setup.register_protocol');self.registration_mock=self.registration.start();self.addCleanup(self.registration.stop)
  self.lock=patch('setup.manager_lock',side_effect=nullcontext);self.lock.start();self.addCleanup(self.lock.stop)
 def test_fresh_install_does_not_touch_game(self):
  install(self.root,self.payload,self.tools)
  self.assertEqual((self.root/'data.win').read_bytes(),b'currently modded game')
  self.assertEqual((self.root/'Tribal Hunter Mod Manager.exe').read_bytes(),b'new manager')
  self.assertEqual((self.root/'mod-manager/tools/umt/UndertaleModCli.exe').read_bytes(),b'compiler')
  self.assertFalse((self.root/'mod-manager/state.json').exists())
  self.assertFalse((self.root/'mod-manager/mods').exists())
 def test_upgrade_preserves_mods_backups_and_settings(self):
  home=self.root/'mod-manager';(home/'mods/example').mkdir(parents=True);(home/'backups').mkdir()
  (home/'mods/example/data.win').write_bytes(b'mod package')
  (home/'backups/data.win').write_bytes(b'clean original')
  (home/'state.json').write_text('{"active":["example"]}')
  (self.root/'tribal_visuals.ini').write_text('settings')
  before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
  install(self.root,self.payload,self.tools)
  for path,data in before.items():self.assertEqual((self.root/path).read_bytes(),data)
 def test_bad_folder_does_not_create_manager(self):
  wrong=self.base/'wrong';wrong.mkdir()
  with self.assertRaisesRegex(ValueError,'game folder'):install(wrong,self.payload,self.tools)
  self.assertFalse((wrong/'mod-manager').exists())
 def test_incomplete_payload_stays_untouched(self):
  with self.assertRaisesRegex(ValueError,'incomplete'):install(self.root,self.base/'missing',self.tools)
  self.assertFalse((self.root/'mod-manager').exists())
 def test_failure_restores_existing_manager_and_compiler(self):
  exe=self.root/'Tribal Hunter Mod Manager.exe';exe.write_bytes(b'old manager')
  tools=self.root/'mod-manager/tools/umt';tools.mkdir(parents=True);(tools/'UndertaleModCli.exe').write_bytes(b'old compiler')
  import setup
  replace=setup.os.replace
  def fail(source,dest):
   if Path(source).name=='manager.exe':raise OSError('simulated replacement failure')
   return replace(source,dest)
  with patch('setup.os.replace',side_effect=fail):
   with self.assertRaisesRegex(OSError,'simulated'):install(self.root,self.payload,self.tools)
  self.assertEqual(exe.read_bytes(),b'old manager')
  self.assertEqual((tools/'UndertaleModCli.exe').read_bytes(),b'old compiler')
  self.assertEqual((self.root/'data.win').read_bytes(),b'currently modded game')
 def test_reinstall_updates_only_app_and_tools(self):
  install(self.root,self.payload,self.tools)
  self.payload.write_bytes(b'updated manager');install(self.root,self.payload,self.tools)
  self.assertEqual((self.root/'Tribal Hunter Mod Manager.exe').read_bytes(),b'updated manager')
  self.assertEqual((self.root/'data.win').read_bytes(),b'currently modded game')
  self.assertFalse(list(self.root.glob('.mod-manager-setup-*')))

 def test_registration_and_guides_installed_for_selected_game(self):
  guides=self.base/'guides';guides.mkdir();(guides/'README.md').write_text('usage');(guides/'MAKING-MODS.md').write_text('author tutorial')
  install(self.root,self.payload,self.tools,guides=guides)
  self.registration_mock.assert_called_once_with(self.root/'Tribal Hunter Mod Manager.exe')
  self.assertEqual((self.root/'mod-manager/MAKING-MODS.md').read_text(),'author tutorial')
 def test_registration_failure_rolls_back_app_tools_and_guides(self):
  exe=self.root/'Tribal Hunter Mod Manager.exe';exe.write_bytes(b'old manager')
  tools=self.root/'mod-manager/tools/umt';tools.mkdir(parents=True);(tools/'UndertaleModCli.exe').write_bytes(b'old compiler')
  guide=self.root/'mod-manager/README.md';guide.write_text('old guide')
  guides=self.base/'guides';guides.mkdir();(guides/'README.md').write_text('new guide');(guides/'MAKING-MODS.md').write_text('new author tutorial')
  self.registration_mock.side_effect=OSError('registration failed')
  with self.assertRaisesRegex(OSError,'registration failed'):install(self.root,self.payload,self.tools,guides=guides)
  self.assertEqual(exe.read_bytes(),b'old manager');self.assertEqual((tools/'UndertaleModCli.exe').read_bytes(),b'old compiler')
  self.assertEqual(guide.read_text(),'old guide');self.assertFalse((self.root/'mod-manager/MAKING-MODS.md').exists())
  self.assertEqual((self.root/'data.win').read_bytes(),b'currently modded game')

if __name__=='__main__':unittest.main(verbosity=2)
