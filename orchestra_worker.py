import json, os, re, subprocess, time, urllib.request
from pathlib import Path

API='http://127.0.0.1:8080/v1/chat/completions'
ROLE=os.environ['ORCHESTRA_ROLE']
MODEL=os.environ['MODEL_SLUG']
INPUT_FILE=os.environ.get('ORCHESTRA_INPUT','')
OUT=Path(os.environ.get('ORCHESTRA_OUT','orchestra-output'))
OUT.mkdir(parents=True,exist_ok=True)
MAX_STEPS={'scout':9,'verifier':12,'patcher':14,'reviewer':12}.get(ROLE,10)
MAX_TURNS=MAX_STEPS+4

ROLE_PROMPTS={
'scout': '''You are an independent Rails code-quality scout. Explore the disposable Rails app in /app. Look for concrete authorization mistakes, dangerous defaults, validation or parameter-handling defects, persistence mistakes, and broken behavior. Do not patch. Use executed Rails/tests/code inspection as evidence. Final JSON: {"action":"final","candidates":[{"title":"...","claim":"...","files":["..."],"evidence":"what you actually observed","proposed_verification":"exact next test"}]}. Return at most 5 candidates.''',
'verifier': '''You are an independent defect verifier. You receive hypotheses from scouts. Assume each may be wrong. Use the disposable Rails app in /app to reproduce or falsify them with Rails runner commands or tests. You may create temporary tests. Do not modify production code. Final JSON: {"action":"final","verified":[{"title":"...","status":"verified|falsified|inconclusive","severity":"low|medium|high|critical","claim":"...","proof":"specific executed evidence","recommended_fix":"..."}]}. Only mark verified when the behavior was actually reproduced.''',
'patcher': '''You are the implementation engineer. You receive independently verified defects. Reproduce them, make the smallest correct production-code fix in /app, add regression tests, and run the relevant/full test suite. Final JSON: {"action":"final","summary":"...","tests":"commands and outcomes","fixed":["..."]}. Do not claim success unless tests actually pass. The harness captures git diff separately.''',
'reviewer': '''You are an independent patch reviewer. A proposed patch has already been applied to /app. Inspect the diff, run existing tests, add temporary edge-case tests if useful, and look for regressions or incomplete fixes. Do not modify production code. Final JSON: {"action":"final","verdict":"accept|reject|inconclusive","reasons":["..."],"tests":"executed evidence"}.'''
}
SYSTEM='''You operate only inside an authorized disposable Rails benchmark container. External network access is unavailable. Do not access files outside the app container. Return exactly one JSON object per turn. To execute a local diagnostic/test command: {"action":"shell","command":"...","purpose":"..."}. To finish, follow the role-specific final schema. Never invent command output or evidence.''' + '\n\n' + ROLE_PROMPTS[ROLE]
context=''
if INPUT_FILE and Path(INPUT_FILE).exists(): context=Path(INPUT_FILE).read_text(errors='replace')[:30000]
USER='Begin the assigned role. The disposable app is at /app.' + ('\n\nUpstream structured context:\n'+context if context else '')

def call(messages,max_tokens=1100):
    req=urllib.request.Request(API,data=json.dumps({'model':MODEL,'messages':messages,'temperature':0.1,'max_tokens':max_tokens,'stream':False}).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=900) as r: d=json.load(r)
    c=d['choices'][0]; m=c['message']; text=(m.get('content') or m.get('reasoning_content') or m.get('reasoning') or '').strip()
    return text,c.get('finish_reason'),d.get('usage',{}),d.get('timings',{})

def parse(text):
    try: return json.loads(text)
    except Exception:
        m=re.search(r'\{.*\}',text,re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None

def shell(cmd):
    t=time.time()
    try:
        p=subprocess.run(['docker','exec','freshfruit-app','bash','-lc',cmd],capture_output=True,text=True,timeout=150)
        out=p.stdout+('\nSTDERR:\n'+p.stderr if p.stderr else '')
        return p.returncode,out[-24000:],round(time.time()-t,2)
    except subprocess.TimeoutExpired:
        return 124,'TIMEOUT',round(time.time()-t,2)

messages=[{'role':'system','content':SYSTEM},{'role':'user','content':USER}]
events=[]; steps=0; final=None
for turn in range(1,MAX_TURNS+1):
    raw,finish,usage,timings=call(messages)
    events.append({'type':'model','turn':turn,'raw':raw,'finish_reason':finish,'usage':usage,'timings':timings})
    obj=parse(raw)
    if obj and obj.get('action')=='final': final=obj; break
    if obj and obj.get('action')=='shell' and obj.get('command') and steps<MAX_STEPS:
        rc,out,secs=shell(obj['command']); steps+=1
        events.append({'type':'shell','step':steps,'command':obj['command'],'purpose':obj.get('purpose',''),'exit_code':rc,'seconds':secs,'output':out})
        messages += [{'role':'assistant','content':raw},{'role':'user','content':f'SHELL_RESULT step={steps} exit_code={rc}\n{out[-5500:]}'}]
        continue
    messages += [{'role':'assistant','content':raw},{'role':'user','content':'Invalid format. Return one action=shell JSON object, or the role-specific action=final JSON.'}]
if final is None:
    raw,finish,usage,timings=call(messages+[{'role':'user','content':'Stop now and return the role-specific action=final JSON only.'}],1600)
    events.append({'type':'forced_final','raw':raw,'finish_reason':finish,'usage':usage,'timings':timings})
    final=parse(raw) or {'action':'final','error':'invalid final response','raw':raw}
result={'role':ROLE,'model':MODEL,'shell_steps':steps,'final':final,'events':events}
(OUT/'result.json').write_text(json.dumps(result,indent=2))
(OUT/'final.json').write_text(json.dumps(final,indent=2))
p=subprocess.run(['docker','exec','freshfruit-app','bash','-lc','git diff -- .'],capture_output=True,text=True)
(OUT/'target.diff').write_text(p.stdout)
print(json.dumps({'role':ROLE,'model':MODEL,'shell_steps':steps,'final':final},indent=2))
