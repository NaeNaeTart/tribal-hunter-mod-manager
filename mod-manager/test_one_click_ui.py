from pathlib import Path
import tempfile,time,threading
from unittest.mock import patch
import desktop_ui, one_click

with tempfile.TemporaryDirectory() as work,patch.object(desktop_ui,'game_running',return_value=False):
 root=Path(work)/'game';root.mkdir();(root/'data.win').write_bytes(b'original')
 source=Path(work)/'mod';source.mkdir();(source/'data.win').write_bytes(b'modified');(source/'mod.json').write_text('{"name":"One-click fixture","version":"1.0.0"}')
 app=desktop_ui.App(root);errors=[];app.report_callback_exception=lambda kind,value,trace:errors.append(str(value))
 calls=[];gate=threading.Event()
 def import_fixture(manager,uri,progress):
  calls.append(uri);progress('Downloading fixture...')
  if len(calls)==1:gate.wait(5)
  return manager.import_mod(source)
 def pump_until(condition,seconds=6):
  until=time.monotonic()+seconds
  while not condition() and time.monotonic()<until:app.update();time.sleep(0.01)
  assert condition(),'GUI request timed out'
 with patch.object(one_click,'import_link',side_effect=import_fixture):
  one_click.queue_link(root,'tribalhunter:https://gamebanana.com/mmdl/123,Mod,456')
  pump_until(lambda:app.busy and len(calls)==1)
  one_click.queue_link(root,'tribalhunter:https://gamebanana.com/mmdl/124,Mod,457')
  gate.set()
  pump_until(lambda:len(calls)==2 and not app.busy)
  assert len(app.entries)==2 and app.selected in [m['id'] for m in app.entries]
  assert (root/'data.win').read_bytes()==b'original' and app.manager.state['active']==[]
  assert one_click.take_link(root) is None
 app.show_page('Guide')
 with patch.object(one_click,'register_protocol') as register:
  app.register_one_click();register.assert_called_once_with(root/'Tribal Hunter Mod Manager.exe')
 assert not errors,errors
 app.destroy()
print('PASS live GUI one-click queue, busy forwarding, progress, selected import and registration control; game unchanged')
