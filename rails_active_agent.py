import ast, json, os, re, subprocess, time, urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8080/v1/chat/completions'
MODEL = os.environ['MODEL_SLUG']
LABEL = os.environ['MODEL_LABEL']
GITHUB_RUN_ID = os.environ.get('GITHUB_RUN_ID')
MAX_TURNS = 14
MAX_SHELL_STEPS = 14
TURN_TOKENS = 600
FINAL_TOKENS = 800
MODEL_SHELL_OUTPUT_CHARS = 5000

OUT = Path('active-output')
OUT.mkdir(exist_ok=True)
EVENTS = OUT / 'events.jsonl'

SYSTEM = '''You are an authorized senior Ruby on Rails security/debugging engineer operating inside a disposable isolated copy of a Rails app. Find and PROVE real bugs, security flaws, broken behavior, or dangerous defaults by inspecting and executing the app. Prefer executable evidence: Rails runner output, tests, request/response behavior, Brakeman, or minimal reproductions. You may edit files and add temporary tests. Do not access anything outside this container or any external network.

Return exactly one JSON object per turn.
Use a shell command:
{"action":"shell","command":"...","purpose":"..."}
As soon as you have a substantiated finding, persist it immediately instead of waiting for the end:
{"action":"finding","title":"...","severity":"low|medium|high|critical","claim":"...","evidence_steps":[1,2],"fix":"..."}
Then continue investigating. evidence_steps must refer only to shell steps you actually ran.
When completely finished, keep the final response short:
{"action":"final","summary":"..."}
Do not repeat all findings in the final response; findings are persisted incrementally. Prefer demonstrated behavior over speculation.'''

USER = '''You have a disposable sanitized legacy Rails application modeled on a real Rails 6.1 / Ruby 3.0.x codebase. PostgreSQL test DB is available. Work from /app. Investigate as an engineer would: inspect code, boot Rails, run tests/scanners/rails runner, create temporary reproduction tests if useful, and prove concrete findings. Focus on auth/authorization, dangerous defaults, parameter handling, persistence semantics, and broken application behavior. Do not assume any disclosed answer key.'''

def persist(event):
    event = dict(event)
    event.setdefault('github_run_id', GITHUB_RUN_ID)
    event.setdefault('model_slug', MODEL)
    event.setdefault('ts', time.time())
    with EVENTS.open('a') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')

def call_model(messages, max_tokens=TURN_TOKENS):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.load(r)
    msg = data['choices'][0]['message']
    text = (msg.get('content') or msg.get('reasoning_content') or msg.get('reasoning') or '').strip()
    return text, data.get('timings', {})

def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None

def parse_native_shell_calls(text):
    if '<|tool_call_start|>' not in text or 'shell(' not in text:
        return []
    calls = []
    for m in re.finditer(r"shell\(command=(('(?:\\.|[^'])*')|(\"(?:\\.|[^\"])*\"))\)", text, re.S):
        try:
            cmd = ast.literal_eval(m.group(1))
        except Exception:
            continue
        if isinstance(cmd, str) and cmd.strip():
            calls.append(cmd)
    return calls

def run_shell(command):
    started = time.time()
    try:
        p = subprocess.run(['docker','exec','freshfruit-app','bash','-lc',command], capture_output=True, text=True, timeout=120)
        out = p.stdout + ('\nSTDERR:\n' + p.stderr if p.stderr else '')
        return p.returncode, out[-20000:], round(time.time()-started, 2)
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        stderr = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        return 124, (stdout + '\nTIMEOUT\n' + stderr)[-20000:], round(time.time()-started, 2)

def finding_with_evidence(obj, shell_steps):
    refs = obj.get('evidence_steps') or []
    finding = {
        'title': obj.get('title','Untitled'),
        'severity': obj.get('severity','?'),
        'claim': obj.get('claim',''),
        'evidence_steps': refs,
        'fix': obj.get('fix',''),
    }
    finding['mechanically_evidenced'] = bool(refs) and all(isinstance(n,int) and n in shell_steps for n in refs)
    return finding

messages=[{"role":"system","content":SYSTEM},{"role":"user","content":USER}]
transcript=[]
recorded_findings=[]
final_obj=None
shell_count=0
shell_steps={}

persist({'type':'session_start','model_label':LABEL})

for turn in range(1, MAX_TURNS + 1):
    raw, timings = call_model(messages)
    model_rec = {"turn":turn,"type":"model","raw":raw,"timings":timings}
    transcript.append(model_rec)
    persist(model_rec)
    obj = parse_json(raw)

    if obj and obj.get('action') == 'finding':
        finding = finding_with_evidence(obj, shell_steps)
        recorded_findings.append(finding)
        persist({'type':'finding','finding':finding})
        messages.append({"role":"assistant","content":raw})
        messages.append({"role":"user","content":"Finding persisted. Continue investigating, or return action=final when finished."})
        continue

    if obj and obj.get('action') == 'final':
        final_obj = obj
        # Backward-compatible fallback if a model still puts findings in final.
        for f in obj.get('findings', []) or []:
            finding = finding_with_evidence(f, shell_steps)
            recorded_findings.append(finding)
            persist({'type':'finding','finding':finding,'source':'final_fallback'})
        break

    commands=[]
    purpose=''
    if obj and obj.get('action') == 'shell' and obj.get('command'):
        commands=[obj['command']]
        purpose=obj.get('purpose','')
    else:
        commands=parse_native_shell_calls(raw)
        if commands:
            purpose='native model tool call'

    if commands:
        results=[]
        for command in commands:
            if shell_count >= MAX_SHELL_STEPS:
                break
            rc,out,secs=run_shell(command)
            shell_count += 1
            rec={"step":shell_count,"type":"shell","command":command,"purpose":purpose,"exit_code":rc,"seconds":secs,"output":out}
            shell_steps[shell_count]=rec
            transcript.append(rec)
            persist(rec)
            results.append(f"SHELL_RESULT step={shell_count} exit_code={rc}\n{out[-MODEL_SHELL_OUTPUT_CHARS:]}")
        messages.append({"role":"assistant","content":raw})
        if results:
            messages.append({"role":"user","content":"\n\n".join(results)})
        if shell_count >= MAX_SHELL_STEPS:
            break
        continue

    messages.append({"role":"assistant","content":raw})
    messages.append({"role":"user","content":"Invalid format. Use action=shell, action=finding, or action=final. Native shell tool-call syntax is also accepted."})

if final_obj is None:
    final_prompt = '''Stop investigating. First, if you have any substantiated finding that you have NOT already persisted with action=finding, return ONE action=finding JSON now. Otherwise return only {"action":"final","summary":"concise summary"}. Do not use markdown.'''
    raw, timings = call_model(messages + [{"role":"user","content":final_prompt}], max_tokens=FINAL_TOKENS)
    persist({"type":"final_model","raw":raw,"timings":timings})
    obj = parse_json(raw)
    if obj and obj.get('action') == 'finding':
        finding = finding_with_evidence(obj, shell_steps)
        recorded_findings.append(finding)
        persist({'type':'finding','finding':finding,'source':'forced_final'})
        final_obj={'action':'final','summary':'Final finding persisted incrementally.'}
    elif obj and obj.get('action') == 'final':
        final_obj=obj
        for f in obj.get('findings', []) or []:
            finding=finding_with_evidence(f, shell_steps)
            recorded_findings.append(finding)
            persist({'type':'finding','finding':finding,'source':'final_fallback'})
    else:
        final_obj={"action":"final","summary":"Model did not produce valid final JSON","raw":raw}

# De-duplicate repeated findings while preserving first occurrence.
deduped=[]
seen=set()
for f in recorded_findings:
    key=(f.get('title','').strip().lower(), f.get('claim','').strip().lower())
    if key in seen:
        continue
    seen.add(key)
    deduped.append(f)
verified=[f for f in deduped if f.get('mechanically_evidenced')]

persist({'type':'session_end','verified_finding_count':len(verified),'finding_count':len(deduped),'summary':final_obj.get('summary','')})
result={"github_run_id":GITHUB_RUN_ID,"model":LABEL,"slug":MODEL,"verified_finding_count":len(verified),"finding_count":len(deduped),"shell_step_count":shell_count,"final":final_obj,"findings":deduped,"transcript":transcript}
(OUT/'result.json').write_text(json.dumps(result,indent=2))

lines=[f'# Active Rails agent benchmark — {LABEL}','',f'Verified-evidence findings: **{len(verified)}**',f'Findings persisted: **{len(deduped)}**',f'Shell steps executed: **{shell_count}**','',final_obj.get('summary',''),'']
for i,f in enumerate(deduped,1):
    lines += [f"## {i}. {f.get('title','Untitled')}",f"Severity: {f.get('severity','?')} — mechanically evidenced: **{f.get('mechanically_evidenced',False)}**",'',f.get('claim',''),'',f"Evidence steps: {f.get('evidence_steps',[])}",'',f"Fix: {f.get('fix','')}",'']
lines += ['# Transcript','']
for x in transcript:
    if x.get('type')=='shell':
        lines += [f"## Shell step {x['step']}",f"`{x['command']}`",f"exit={x['exit_code']} time={x['seconds']}s",'','```text',x['output'],'```','']
(OUT/'report.md').write_text('\n'.join(lines))
print(json.dumps({"model":LABEL,"verified_finding_count":len(verified),"finding_count":len(deduped),"shell_step_count":shell_count,"findings":deduped},indent=2))
