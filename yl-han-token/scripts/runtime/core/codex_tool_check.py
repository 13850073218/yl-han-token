"""Verify a full namespaced tool round trip using the installed app-server API."""
import json
import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import time


def check_tool(cfg, timeout=210):
    from agents import codex
    with tempfile.TemporaryDirectory(prefix="hantoken-native-tool-") as root:
        home=pathlib.Path(root)
        (home/'models.json').write_text(json.dumps({'models':[cfg.get('model_info') or codex._custom_model(cfg)]}),encoding='utf8')
        doc=codex._tomlkit().document()
        doc.update(model=cfg['id'],model_provider='probe',model_catalog_json=str(home/'models.json'),
                   model_reasoning_effort=cfg.get('reasoning_effort','none'))
        if not cfg.get('desktop_tools'):
            doc['web_search']='disabled'
            doc['features']={'multi_agent':False,'code_mode':False,'apps':False}
        doc['model_providers']={'probe':{'name':'Probe','base_url':cfg['url'],'wire_api':'responses',
            'env_key':'HANTOKEN_CHECK_KEY','requires_openai_auth':False,'request_max_retries':0,'stream_max_retries':0}}
        (home/'config.toml').write_text(codex._tomlkit().dumps(doc),encoding='utf8')
        process=subprocess.Popen([codex._find_cli(),'app-server','--stdio'],cwd=root,
            env=dict(os.environ,CODEX_HOME=root,HANTOKEN_CHECK_KEY=cfg['api_key']),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf8')
        messages=queue.Queue()
        def reader():
            for line in process.stdout:
                try:messages.put(json.loads(line))
                except ValueError:pass
        worker=threading.Thread(target=reader,daemon=True);worker.start()
        def send(value):
            process.stdin.write(json.dumps(value)+'\n');process.stdin.flush()
        def request(i,method,params):
            send({'id':i,'method':method,'params':params})
            while True:
                response=messages.get(timeout=20)
                if response.get('id')==i:
                    if 'error' in response:raise ValueError('App-server rejected test setup')
                    return response['result']
        called,answered=False,False
        try:
            request(1,'initialize',{'clientInfo':{'name':'hantoken_check','version':'1'},'capabilities':{'experimentalApi':True}})
            tool={'type':'namespace','name':'hantoken','description':'Compatibility test tools','tools':[{
                'type':'function','name':'echo','description':'Return the supplied text unchanged.',
                'inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}]}
            thread=request(2,'thread/start',{'model':cfg['id'],'ephemeral':True,'cwd':root,
                'approvalPolicy':'never','sandbox':'read-only','dynamicTools':[tool]})['thread']['id']
            request(3,'turn/start',{'threadId':thread,'input':[{'type':'text',
                'text':'Call hantoken.echo exactly once with text HANTOKEN_OK, then repeat the returned text. Do not call other tools.'}]})
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                event=messages.get(timeout=max(0.1,deadline-time.monotonic()))
                if event.get('method')=='item/tool/call':
                    params=event['params']
                    valid=params.get('namespace')=='hantoken' and params.get('tool')=='echo' and params.get('arguments')=={'text':'HANTOKEN_OK'}
                    called|=valid
                    send({'id':event['id'],'result':{'success':valid,'contentItems':[{'type':'inputText','text':'HANTOKEN_OK' if valid else 'Invalid test tool call'}]}})
                elif event.get('method')=='item/completed':
                    item=event.get('params',{}).get('item',{})
                    answered|=item.get('type')=='agentMessage' and 'HANTOKEN_OK' in item.get('text','')
                elif event.get('method')=='turn/completed':
                    status=event['params']['turn'].get('status')
                    result={'probe':'codex_tool','ok':called and answered and status=='completed',
                            'tool_called':called,'answered':answered,'turn_status':status}
                    error=event['params']['turn'].get('error')
                    if isinstance(error,dict):
                        result['reason']=str(error.get('message','')).replace(cfg['api_key'],'[REDACTED]')[:500]
                    return result
            return {'probe':'codex_tool','ok':False,'reason':'Timed out','tool_called':called,'answered':answered}
        except (ValueError,queue.Empty,OSError):
            return {'probe':'codex_tool','ok':False,'reason':'Test setup failed or timed out','tool_called':called,'answered':answered}
        finally:
            process.terminate();process.wait(timeout=5);worker.join(timeout=5)
            process.stdin.close();process.stdout.close()
