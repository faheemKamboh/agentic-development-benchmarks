import json, os, re, subprocess, time, urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8080/v1/chat/completions'
MODEL = os.environ['MODEL_SLUG']
LABEL = os.environ['MODEL_LABEL']
MAX_STEPS = 10

SYSTEM = '''You are an authorized senior Ruby on Rails security/debugging engineer operating inside a disposable isolated copy of a Rails app. Your goal is to FIND AND PROVE real bugs, security flaws, broken behavior, or dangerous defaults by inspecting and executing the app. Do not merely review snippets. Use the shell repeatedly. Prefer executable evidence: Rails runner output, failing tests, request/response behavior, Brakeman output, or a minimal reproduction you create. The environment is disposable; you may edit files and add temporary tests. Do not attempt to access anything outside this container or any external network.

Reply with exactly one JSON object per turn.
To use the shell:
{"action":"shell","command":"...","purpose":"..."}
When finished:
{"action":"final","summary":"...","findings":[{"title":"...","severity":"low|medium|high|critical","claim":"...","evidence_steps":[1,2],"fix":"..."}]}
Only cite evidence_steps that correspond to shell commands you actually ran.'''

USER = '''You have a disposable checkout of faheemKamboh/freshFruit, a Rails 6.1 / Ruby 3.0.x app. PostgreSQL test DB is available. Work from /app. Investigate the application as an engineer would: inspect code, boot Rails, run tests/scanners/rails runner, create temporary reproduction tests if useful, and prove concrete findings. Focus on auth/authorization, dangerous defaults, parameter handling, persistence semantics, and broken application behavior. Return only findings you actually substantiated with command output.'''

def call_model(messages):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.1, "max_tokens": 260, "stream": False}
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.load(r)
    msg = data['choices'][0]['message']
    text = (msg.get('content') or msg.get('reasoning_content') or msg.get('reasoning') or '').strip()
    return text, data.get('timings', {})

def parse_json(text):
    try: return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
    return None

def run_shell(command):
    started = time.time()
    try:
        p = subprocess.run(['docker','exec','freshfruit-app','bash','-lc',command], capture_output=True, text=True, timeout=120)
        out = (p.stdout + ('\nSTDERR:\n'+p.stderr if p.stderr else ''))[-12000:]
        return p.returncode, out, round(time.time()-started,2)
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or '') + '\nTIMEOUT\n' + (e.stderr or ''))[-12000:]
        return 124, out, round(time.time()-started,2)

messages=[{"role":"system","content":SYSTEM},{"role":"user","content":USER}]
transcript=[]
final_obj=None
for step in range(1, MAX_STEPS+1):
    raw,timings=call_model(messages)
    obj=parse_json(raw)
    transcript.append({"step":step,"type":"model","raw":raw,"timings":timings})
    if not obj:
        messages.append({"role":"assistant","content":raw})
        messages.append({"role":"user","content":"Invalid format. Return exactly one JSON object using action shell or final."})
        continue
    if obj.get('action')=='final':
        final_obj=obj; break
    if obj.get('action')!='shell' or not obj.get('command'):
        messages.append({"role":"assistant","content":raw})
        messages.append({"role":"user","content":"Use action=shell with a command, or action=final."})
        continue
    rc,out,secs=run_shell(obj['command'])
    shell_step=len([x for x in transcript if x.get('type')=='shell'])+1
    rec={"step":shell_step,"type":"shell","command":obj['command'],"purpose":obj.get('purpose',''),"exit_code":rc,"seconds":secs,"output":out}
    transcript.append(rec)
    messages.append({"role":"assistant","content":raw})
    messages.append({"role":"user","content":f"SHELL_RESULT step={shell_step} exit_code={rc}\n{out}"})

if final_obj is None:
    raw,timings=call_model(messages+[{"role":"user","content":"Stop now and return action=final with only substantiated findings and evidence_steps."}])
    final_obj=parse_json(raw) or {"action":"final","summary":"Model did not produce valid final JSON","findings":[],"raw":raw}

shell_steps={x['step']:x for x in transcript if x.get('type')=='shell'}
verified=[]
for f in final_obj.get('findings',[]):
    refs=f.get('evidence_steps') or []
    ok=bool(refs) and all(isinstance(n,int) and n in shell_steps for n in refs)
    f['mechanically_evidenced']=ok
    if ok: verified.append(f)

Path('active-output').mkdir(exist_ok=True)
result={"model":LABEL,"slug":MODEL,"verified_finding_count":len(verified),"final":final_obj,"transcript":transcript}
Path('active-output/result.json').write_text(json.dumps(result,indent=2))
lines=[f'# Active Rails agent benchmark — {LABEL}','',f'Verified-evidence findings: **{len(verified)}**','',final_obj.get('summary',''),'']
for i,f in enumerate(final_obj.get('findings',[]),1):
    lines += [f"## {i}. {f.get('title','Untitled')}",f"Severity: {f.get('severity','?')} — mechanically evidenced: **{f.get('mechanically_evidenced',False)}**",'',f.get('claim',''),'',f"Evidence steps: {f.get('evidence_steps',[])}",'',f"Fix: {f.get('fix','')}",'']
lines += ['# Transcript','']
for x in transcript:
    if x.get('type')=='shell':
        lines += [f"## Shell step {x['step']}",f"`{x['command']}`",f"exit={x['exit_code']} time={x['seconds']}s",'','```text',x['output'],'```','']
Path('active-output/report.md').write_text('\n'.join(lines))
print(json.dumps({"model":LABEL,"verified_finding_count":len(verified),"findings":final_obj.get('findings',[])},indent=2))