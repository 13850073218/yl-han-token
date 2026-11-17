"""Offline integration checks; no actual credentials, network or client changes."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from connection_input import normalize,AGENTS
from connect import apply_opencode,probe,NoRedirect
KEY='test-only-not-real'
def payload(**kw): return json.dumps(dict(apiKey=KEY,**kw))
class Response(BytesIO): status=200
class Connections(unittest.TestCase):
 def test_defaults(self):
  for a in AGENTS:
   c=normalize(payload(id='Exact-ID'),a)[0]
   self.assertEqual(c['url'],'https://api.hanrouter.ai/v1'+('/chat/completions' if a=='WorkBuddy' else ''))
   self.assertEqual(c['id'],'Exact-ID')
 def test_aliases(self):
  c=normalize(json.dumps({'key':KEY,'modelId':'ID'}),'Codex')[0]
  self.assertEqual((c['id'],c['apiKey']),('ID',KEY))
 def test_batch_exact_case(self):
  c=normalize(payload(models=['MiniMax-M3','minimax-m3',{'id':'other','apiKey':'different','url':'https://example.test/v1'}]),'OpenCode')
  self.assertEqual(len(c),3);self.assertEqual(c[-1]['apiKey'],'different');self.assertEqual(c[0]['apiKey'],KEY)
 def test_duplicates(self):
  c=normalize(json.dumps([{'id':'A','apiKey':KEY},{'id':'A','apiKey':'replacement'}]),'Codex')
  self.assertEqual(len(c),1);self.assertEqual(c[0]['apiKey'],'replacement')
 def test_paths(self):
  for a in ['Codex','WorkBuddy','OpenCode']:
   c=normalize(payload(id='A',url='https://example.test/custom/v1/chat/completions/'),a)[0]
   self.assertEqual(c['url'],'https://example.test/custom/v1'+('/chat/completions' if a=='WorkBuddy' else ''))
 def test_unsafe_urls(self):
  for u in ['http://example.test','https://user:pass@example.test','https://example.test?key=secret','https://example.test/#key']:
   with self.assertRaises(ValueError):normalize(payload(id='A',url=u),'Codex')
 def test_missing_fields(self):
  for c in [{'id':'A'},{'key':KEY},{'key':KEY,'models':[]}]:
   with self.assertRaises(ValueError):normalize(json.dumps(c),'Codex')
 def test_merge_backup_idempotent_defaults(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'opencode.json';old={'model':'existing/normal','provider':{'existing':{'npm':'other'}},'theme':'custom'};p.write_text(json.dumps(old))
   configs=normalize(payload(models=['MiniMax-M3','minimax-m3']),'OpenCode')
   self.assertTrue(apply_opencode(configs,p,True)['backupCreated']);doc=json.loads(p.read_text())
   self.assertEqual(doc['model'],old['model']);self.assertEqual(doc['provider']['existing'],old['provider']['existing']);self.assertEqual(len(doc['provider']),3)
   self.assertEqual(json.loads(list(Path(d).glob('*.backup-*'))[0].read_text()),old)
   self.assertFalse(apply_opencode(configs,p,True)['written']);self.assertEqual(len(list(Path(d).glob('*.backup-*'))),1)
   apply_opencode(configs,p);self.assertTrue(json.loads(p.read_text())['model'].endswith('/MiniMax-M3'))
 def test_malformed_json_and_jsonc_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'opencode.json';c=normalize(payload(id='A'),'OpenCode');p.write_text('{invalid')
   with self.assertRaises(ValueError):apply_opencode(c,p)
   self.assertEqual(p.read_text(),'{invalid');p.unlink();p.with_suffix('.jsonc').write_text('{}')
   with self.assertRaises(ValueError):apply_opencode(c,p)
   self.assertFalse(p.exists())
 def test_probe_exact_case(self):
  with patch('urllib.request.OpenerDirector.open',return_value=Response(b'{"data":[{"id":"MiniMax-M3"}]}')):
   r=probe(normalize(payload(id='minimax-m3'),'Codex')[0])
  self.assertFalse(r['ok']);self.assertIn('generation not verified',r['note'])
 def test_empty_chat_not_success(self):
  with patch('urllib.request.OpenerDirector.open',return_value=Response(b'{"choices":[{"message":{"content":""}}]}')):
   r=probe(normalize(payload(id='A'),'Codex')[0],True)
  self.assertFalse(r['ok']);self.assertTrue(r['requestAccepted'])
 def test_error_redaction(self):
  e=urllib.error.HTTPError('https://example.test',401,KEY,{},BytesIO(KEY.encode()))
  with patch('urllib.request.OpenerDirector.open',side_effect=e):r=probe(normalize(payload(id='A'),'Codex')[0])
  self.assertNotIn(KEY,json.dumps(r));self.assertEqual(r['reason'],'authentication_failed')
 def test_guide_cli_never_leaks_alias_key(self):
  import subprocess
  script=Path(__file__).resolve().parents[1]/'scripts'/'connect.py'
  r=subprocess.run([sys.executable,str(script),'guide','--agent','Cline','--stdin'],input=json.dumps({'id':'A','apiKey':KEY,'key':'another-secret'}),text=True,capture_output=True)
  self.assertEqual(r.returncode,0);self.assertNotIn(KEY,r.stdout+r.stderr);self.assertNotIn('another-secret',r.stdout+r.stderr)
  self.assertEqual(json.loads(r.stdout)['models'][0]['apiKey'],'****************')
 def test_guide_cli_invalid_input_redacted(self):
  import subprocess
  script=Path(__file__).resolve().parents[1]/'scripts'/'connect.py'
  r=subprocess.run([sys.executable,str(script),'guide','--agent','Cline','--stdin'],input='{"apiKey":"'+KEY+'",',text=True,capture_output=True)
  self.assertEqual(r.returncode,1);self.assertNotIn(KEY,r.stdout+r.stderr)
 def test_no_redirect(self):self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other.test'))
if __name__=='__main__':unittest.main()
