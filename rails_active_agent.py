import ast, json, os, re, subprocess, time, urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8080/v1/chat/completions'
MODEL = os.environ['MODEL_SLUG']
LABEL = os.environ['MODEL_LABEL']
MAX_TURNS = 12
MAX_SHELL_STEPS = 12
TURN_TOKENS = 512
FINAL_TOKENS = 2048
MODEL_SHELL_OUTPUT_CHARS = 4500

OUT = Path('active-output')
OUT.mkdir(exist_ok=True)
EVENTS = OUT / 'events.jsonl'

SYSTEM = '''You are an authorized senior Ruby on Rails security/debugging engineer operating inside a disposable isolated copy of a Rails app. Your goal is to FIND AND PROVE real bugs, security flaws, broken behavior, or dangerous defaults by inspecting and executing the app. Do not merely review snippets. Use the shell repeatedly. Prefer executable evidence: Rails runner output, failing tests, request/response behavior, Brakeman output, or a minimal reproduction you create. The environment is disposable; you may edit files and add temporary tests. Do not attempt to access anything outside this container or any external network.

Reply with exactly one JSON object per turn.
To use the shell:
{"action":"shell","command":"...","purpose":"..."}
When finished:
{"action":"final","summary":"...","findings":[{"title":"...","severity":"low|medium|high|critical","claim":"...","evidence_steps":[1,2],"fix":"..."}]}
Only cite evidence_steps that correspond to shell commands you actually ran. Prefer demonstrated behavior over speculative findings.'''

USER = '''You have a disposable sanitized legacy Rails application modeled on a real Rails 6.1 / Ruby 3.0.x codebase. PostgreSQL test DB is available. Work from /app. Investigate the repository as an engineer would: inspect code, boot Rails, run tests/scanners/rails runner, create temporary reproduction tests if useful, and prove concrete findings. Focus on auth/authorization, dangerous defaults, parameter handling, persistence semantics, and broken application behavior. Do not assume any disclosed answer key. Return only findings you actually substantiated with command output.'''

def persist(event):
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
        literal = m.group(1)
        try:
            cmd = ast.literal_eval(literal)
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
        return p.returncode, out[-20000:], round(time.time()-started,2)
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        stderr = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else (e.stderr or '')
        out = (stdout + '\nTIMEOUT\n' + stderr)[-20000:]
        return 124, out, round(time.time()-started,2)

def execute_shell(command, purpose, transcript, shell_count):
    rc, out, secs = run_shell(command)
    shell_count += 1
    rec = {"step":shell_count,"type":"shell","command":command,"purpose":purpose,"exit_code":rc,"seconds":secs,"output":out}
    transcript.append(rec)
    persist(rec)
    clipped = out[-MODEL_SHELL_OUTPUT_CHARS:]
    return shell_count, rec, f"SHELL_RESULT step={shell_count} exit_code={rc}\n{clipped}"

messages=[{"role":"system","content":SYSTEM},{"role":"user","content":USER}]
transcript=[]
final_obj=None
shell_count=0

for turn in range(1, MAX_TURNS+1):
    raw, timings = call_model(messages)
    model_rec = {"turn":turn,"type":"model","raw":raw,"timings":timings}
    transcript.append(model_rec)
    persist(model_rec)

    obj = parse_json(raw)
    if obj and obj.get('action') == 'final':
        final_obj = obj
        break

    commands = []
    purpose = ''
    if obj and obj.get('action') == 'shell' and obj.get('command'):
        commands = [obj['command']]
        purpose = obj.get('purpose','')
    else:
        commands = parse_native_shell_calls(raw)
        if commands:
            purpose = 'native model tool call'

    if commands:
        result_messages=[]
        for command in commands:
            if shell_count >= MAX_SHELL_STEPS:
                break
            shell_count, rec, result_text = execute_shell(command, purpose, transcript, shell_count)
            result_messages.append(result_text)
        messages.append({"role":"assistant","content":raw})
        if result_messages:
            messages.append({"role":"user","content":"\n\n".join(result_messages)})
        if shell_count >= MAX_SHELL_STEPS:
            break
        continue

    messages.append({"role":"assistant","content":raw})
    messages.append({"role":"user","content":"Invalid format. Return exactly one JSON object using action shell or final. If your chat template emits native shell tool calls, those are also accepted."})

if final_obj is None:
    final_prompt = '''Stop investigating. Return ONLY one complete JSON object with action=final. Include only findings substantiated by shell evidence already observed. Keep each claim and fix concise. Do not include markdown or commentary outside JSON.'''
    raw, timings = call_model(messages + [{"role":"user","content":final_prompt}], max_tokens=FINAL_TOKENS)
    persist({"type":"final_model","raw":raw,"timings":timings})
    final_obj = parse_json(raw) or {"action":"final","summary":"Model did not produce valid final JSON","findings":[],"raw":raw}

shell_steps={x['step']:x for x in transcript if x.get('type')=='shell'}
verified=[]
for f in final_obj.get('findings',[]):
    refs=f.get('evidence_steps') or []
    ok=bool(refs) and all(isinstance(n,int) and n in shell_steps for n in refs)
    f['mechanically_evidenced']=ok
    if ok:
        verified.append(f)

result={"model":LABEL,"slug":MODEL,"verified_finding_count":len(verified),"shell_step_count":shell_count,"final":final_obj,"transcript":transcript}
(OUT/'result.json').write_text(json.dumps(result,indent=2))
lines=[f'# Active Rails agent benchmark — {LABEL}','',f'Verified-evidence findings: **{len(verified)}**',f'Shell steps executed: **{shell_count}**','',final_obj.get('summary',''),'']
for i,f in enumerate(final_obj.get('findings',[]),1):
    lines += [f"## {i}. {f.get('title','Untitled')}",f"Severity: {f.get('severity','?')} — mechanically evidenced: **{f.get('mechanically_evidenced',False)}**",'',f.get('claim',''),'',f"Evidence steps: {f.get('evidence_steps',[])}",'',f"Fix: {f.get('fix','')}",'']
lines += ['# Transcript','']
for x in transcript:
    if x.get('type')=='shell':
        lines += [f"## Shell step {x['step']}",f"`{x['command']}`",f"exit={x['exit_code']} time={x['seconds']}s",'','```text',x['output'],'```','']
(OUT/'report.md').write_text('\n'.join(lines))
print(json.dumps({"model":LABEL,"verified_finding_count":len(verified),"shell_step_count":shell_count,"findings":final_obj.get('findings',[])},indent=2))
