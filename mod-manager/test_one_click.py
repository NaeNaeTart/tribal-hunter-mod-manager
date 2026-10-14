"""One-click download/import boundary tests on tiny temporary game fixtures."""
import hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import one_click as oc
from manager import Manager, digest

URI='tribalhunter:https://gamebanana.com/mmdl/123,Mod,456'

class Response(io.BytesIO):
 def __init__(self,data,url,headers=None):super().__init__(data);self.url=url;self.headers=headers or {}
 def geturl(self):return self.url

class Transport:
 def __init__(self,profile,package):self.profile=profile;self.package=package;self.requests=[];self.download_url='https://filecache35.gamebanana.com/mods/test.zip'
 def open(self,request,timeout):
  self.requests.append(request.full_url)
  if '/ProfilePage' in request.full_url:return Response(json.dumps(self.profile).encode(),request.full_url)
  return Response(self.package,self.download_url,{'Content-Length':str(len(self.package))})

class OneClickTests(unittest.TestCase):
 def setUp(self):
  self.work=tempfile.TemporaryDirectory();self.addCleanup(self.work.cleanup)
  self.root=Path(self.work.name)/'game';self.root.mkdir();(self.root/'data.win').write_bytes(b'clean original')
  self.manager=Manager(self.root)
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('mod.json',json.dumps({'name':'Fixture','version':'1.0.0','original_data_sha256':digest(self.root/'data.win')}));z.writestr('files/data.win',b'modified')
  self.package=b.getvalue()
  self.profile={'_aGame':{'_idRow':14084},'_aFiles':[{'_idRow':123,'_sFile':'fixture.zip','_nFilesize':len(self.package),'_sMd5Checksum':hashlib.md5(self.package).hexdigest()}]}
  self.transport=Transport(self.profile,self.package)
 def test_native_and_encoded_protocol_links(self):
  for uri in [URI,URI.replace('mmdl','dl'),URI.replace('tribalhunter:','tribalhunter://'),'tribalhunter:https%3A%2F%2Fgamebanana.com%2Fmmdl%2F123%2CMod%2C456']:
   self.assertEqual(oc.parse_link(uri),oc.InstallLink('https://gamebanana.com/dl/123',123,456))
 def test_untrusted_links_rejected(self):
  for uri in [URI.replace('https:','http:'),URI.replace('gamebanana.com','evil.test'),URI.replace('gamebanana.com','gamebanana.com@evil.test'),URI.replace('/mmdl/123','/mmdl/../123'),URI.replace(',Mod,',',Tool,'),URI+'\n',URI.replace('123,','123?x=1,'),'tribalhunter:file:///data.win,Mod,456']:
   with self.subTest(uri=uri),self.assertRaises(ValueError):oc.parse_link(uri)
 def test_verified_import_does_not_deploy_and_is_deduplicated(self):
  result=oc.import_link(self.manager,URI,opener=self.transport)
  self.assertEqual((self.root/'data.win').read_bytes(),b'clean original');self.assertEqual(self.manager.state['active'],[])
  self.assertEqual(self.manager.catalog()[0]['original_data_sha256'],digest(self.root/'data.win'))
  self.assertEqual(oc.import_link(self.manager,URI,opener=self.transport),result);self.assertEqual(len(self.manager.catalog()),1)
  self.assertFalse(list(self.manager.home.glob('gamebanana-*')))
 def test_wrong_game_rejected_before_downloading(self):
  self.profile['_aGame']['_idRow']=999
  with self.assertRaisesRegex(ValueError,'not for Tribal Hunter'):oc.import_link(self.manager,URI,opener=self.transport)
  self.assertEqual(len(self.transport.requests),1);self.assertEqual(self.manager.catalog(),[])
 def test_file_must_belong_to_post(self):
  self.profile['_aFiles'][0]['_idRow']=124
  with self.assertRaisesRegex(ValueError,'not an available'):oc.import_link(self.manager,URI,opener=self.transport)
  self.assertEqual(len(self.transport.requests),1)
 def test_non_zip_and_private_mods_rejected(self):
  self.profile['_aFiles'][0]['_sFile']='installer.exe'
  with self.assertRaisesRegex(ValueError,'ZIP'):oc.import_link(self.manager,URI,opener=self.transport)
  self.profile['_bIsPrivate']=True
  with self.assertRaisesRegex(ValueError,'private'):oc.import_link(self.manager,URI,opener=self.transport)
 def test_checksum_failure_does_not_import(self):
  self.profile['_aFiles'][0]['_sMd5Checksum']='0'*32
  with self.assertRaisesRegex(ValueError,'verification failed'):oc.import_link(self.manager,URI,opener=self.transport)
  self.assertEqual(self.manager.catalog(),[]);self.assertFalse(list(self.manager.home.glob('gamebanana-*')))
 def test_truncated_download_is_cleaned(self):
  self.transport.package=self.package[:-1]
  with self.assertRaisesRegex(ValueError,'size'):oc.import_link(self.manager,URI,opener=self.transport)
  self.assertEqual(self.manager.catalog(),[]);self.assertFalse(list(self.manager.home.glob('gamebanana-*')))
 def test_unsupported_redirect_rejected(self):
  self.transport.download_url='https://evil.test/file.zip'
  with self.assertRaisesRegex(ValueError,'redirect'):oc.import_link(self.manager,URI,opener=self.transport)
  for url in ['http://filecache35.gamebanana.com/mods/x.zip','https://filecache35.gamebanana.com.evil.test/x','https://user:pass@gamebanana.com/x']:
   self.assertFalse(oc.allowed_remote(url))
 def test_request_queue_for_running_app(self):
  oc.queue_link(self.root,URI);oc.queue_link(self.root,URI)
  self.assertEqual(oc.take_link(self.root),URI);self.assertIsNone(oc.take_link(self.root))
  with self.assertRaises(ValueError):oc.queue_link(self.root,'tribalhunter:invalid')
 def test_replacement_build_hash_is_enforced(self):
  mod_id=oc.import_link(self.manager,URI,opener=self.transport)
  record=self.manager.mods/mod_id/'mod.json';data=json.loads(record.read_text());data['original_data_sha256']='f'*64;record.write_text(json.dumps(data))
  with patch('manager.game_running',return_value=False),self.assertRaisesRegex(ValueError,'original game build'):self.manager.apply([mod_id])
  self.assertEqual((self.root/'data.win').read_bytes(),b'clean original');self.assertEqual(self.manager.state['active'],[])
 def test_invalid_manifest_hash_rejected(self):
  folder=Path(self.work.name)/'invalid';folder.mkdir();(folder/'data.win').write_bytes(b'mod')
  (folder/'mod.json').write_text('{"original_data_sha256":"nope"}')
  with self.assertRaisesRegex(ValueError,'SHA-256'):self.manager.import_mod(folder)

if __name__=='__main__':unittest.main(verbosity=2)
