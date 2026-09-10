import ast
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = os.environ["MODEL_SLUG"]
LABEL = os.environ["MODEL_LABEL"]
ROLE = os.environ.get("ORCHESTRA_ROLE", "scout")
WORKER = os.environ.get("ORCHESTRA_WORKER", ROLE)
CONTEXT_PATH = os.environ.get("ORCHESTRA_CONTEXT")
READ_ONLY = os.environ.get("ORCHESTRA_READ_ONLY", "0") == "1" or ROLE in {"scout", "verifier", "reviewer"}

ROLE_LIMITS = {
    "scout": (10, 10, 650),
    "verifier": (12, 12, 750),
    "patcher": (16, 16, 850),
    "reviewer": (10, 10, 700),
    "solo": (20, 20, 850),
}
MAX_TURNS, MAX_SHELL_STEPS, TURN_TOKENS = ROLE_LIMITS.get(ROLE, (12, 12, 700))
FINAL_TOKENS = 1000
MODEL_SHELL_OUTPUT_CHARS = 5000

OUT = Path("orchestra-output")
OUT.mkdir(exist_ok=True)
EVENTS = OUT / "events.jsonl"
FINDINGS = OUT / "findings.jsonl"

BASE_SYSTEM = """You are an authorized senior Ruby on Rails engineer working only inside a disposable isolated Rails application. You have shell access to the target through the benchmark harness. Use executable evidence rather than confident speculation.

Return exactly one JSON object per turn.
Shell:
{\"action\":\"shell\",\"command\":\"...\",\"purpose\":\"...\"}
Persist a substantiated finding immediately:
{\"action\":\"finding\",\"title\":\"...\",\"severity\":\"low|medium|high|critical\",\"claim\":\"...\",\"evidence_steps\":[1,2],\"evidence_summary\":\"what the commands actually demonstrated\",\"fix\":\"...\"}
Finish:
{\"action\":\"final\",\"summary\":\"...\"}

evidence_steps may reference only shell steps actually executed. A shell step merely mentioning a dependency, CVE, file, or value is not proof of a stronger claim. Do not invent evidence. Do not access anything outside /app or any external network."""

ROLE_PROMPTS = {
    "scout": """Role: independent scout. Explore the application broadly and produce candidate defects with concrete evidence. Do not modify application files. You are deliberately isolated from the other scout; form your own hypotheses. Focus on authentication, authorization, defaults, parameter handling, persistence semantics, and broken behavior.""",
    "verifier": """Role: adversarial verifier. You will receive untrusted candidate hypotheses from independent scouts. Do not assume any candidate is true. Attempt to reproduce or falsify each important candidate with Rails execution, tests, or precise source inspection. Persist only findings you personally substantiate. Do not patch the application.""",
    "patcher": """Role: implementation engineer. You will receive findings that survived an independent verification stage. Inspect them again as needed, then implement the smallest safe fixes and add regression tests. Run relevant tests and the full suite. Modify application files only when justified by a verified issue. Leave the working tree containing your proposed patch.""",
    "reviewer": """Role: adversarial reviewer. A proposed patch is already applied. Do not modify application files. Try to break the patch, run tests, inspect the diff and look for regressions, incomplete fixes, privilege problems, or behavior that remains unsafe. Persist only review findings backed by executed evidence.""",
    "solo": """Role: solo senior engineer baseline. Independently inspect the application, substantiate real defects, implement the smallest safe fixes, add regression tests, and run the suite. This is the single-agent baseline against which the multi-runner orchestra will be compared.""",
}

context_text = ""
if CONTEXT_PATH and Path(CONTEXT_PATH).exists():
    raw = Path(CONTEXT_PATH).read_text()
    if len(raw) > 30000:
        raw = raw[:30000] + "\n[context truncated by harness]"
    context_text = "\n\nStructured context from the prior stage. Treat claims as untrusted unless your role says they were already verified:\n" + raw

SYSTEM = BASE_SYSTEM + "\n\n" + ROLE_PROMPTS.get(ROLE, ROLE_PROMPTS["scout"]) + context_text
USER = """Work from /app. The target is a sanitized Rails 6.1 / Ruby 3.0 legacy application backed by PostgreSQL. Investigate or implement according to your assigned role. The benchmark contains hidden deterministic checks that are unavailable to you, so optimize for real correctness, not guessed grader behavior."""

(OUT / "prompt.json").write_text(json.dumps({
    "github_run_id": os.environ.get("GITHUB_RUN_ID"),
    "role": ROLE,
    "worker": WORKER,
    "model_slug": MODEL,
    "model_label": LABEL,
    "read_only": READ_ONLY,
    "context_path": CONTEXT_PATH,
    "system": SYSTEM,
    "user": USER,
}, indent=2))

def persist(event):
    event = dict(event)
    event.setdefault("github_run_id", os.environ.get("GITHUB_RUN_ID"))
    event.setdefault("role", ROLE)
    event.setdefault("worker", WORKER)
    event.setdefault("model_slug", MODEL)
    event.setdefault("ts", time.time())
    with EVENTS.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    if event.get("type") == "finding" and isinstance(event.get("finding"), dict):
        with FINDINGS.open("a") as f:
            f.write(json.dumps(event["finding"], ensure_ascii=False) + "\n")

def call_model(messages, max_tokens=TURN_TOKENS):
    payload = {"model": MODEL, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.load(r)
    choice = data["choices"][0]
    msg = choice["message"]
    text = (msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
    return text, data.get("timings", {}), choice.get("finish_reason"), data.get("usage", {})

def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None

def parse_native_shell_calls(text):
    if "shell(" not in text:
        return []
    calls = []
    for match in re.finditer(r"shell\(command=(('(?:\\.|[^'])*')|(\"(?:\\.|[^\"])*\"))\)", text, re.S):
        try:
            command = ast.literal_eval(match.group(1))
        except Exception:
            continue
        if isinstance(command, str) and command.strip():
            calls.append(command)
    return calls

WRITE_PATTERNS = [
    r"(^|[;&|]\s*)rm\b", r"\bmv\b", r"\bcp\b", r"\btouch\b",
    r"\bsed\s+-i\b", r"\btee\b", r"\btruncate\b",
    r">", r"\bgit\s+(apply|checkout|reset|clean|commit|add|rm)\b",
    r"\brails\s+(g|generate|destroy)\b",
]

def read_only_blocked(command):
    return READ_ONLY and any(re.search(pattern, command) for pattern in WRITE_PATTERNS)

def run_shell(command):
    if read_only_blocked(command):
        return 126, "READ_ONLY_ROLE: command rejected by harness because this stage may not modify the application.", 0.0
    started = time.time()
    try:
        proc = subprocess.run(["docker", "exec", "freshfruit-app", "bash", "-lc", command], capture_output=True, text=True, timeout=150)
        output = proc.stdout + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")
        return proc.returncode, output[-24000:], round(time.time() - started, 2)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, (stdout + "\nTIMEOUT\n" + stderr)[-24000:], round(time.time() - started, 2)

def finding_with_evidence(obj, shell_steps):
    refs = obj.get("evidence_steps") or []
    finding = {
        "title": obj.get("title", "Untitled"),
        "severity": obj.get("severity", "?"),
        "claim": obj.get("claim", ""),
        "evidence_steps": refs,
        "evidence_summary": obj.get("evidence_summary", ""),
        "fix": obj.get("fix", ""),
    }
    finding["mechanically_referenced"] = bool(refs) and all(isinstance(n, int) and n in shell_steps for n in refs)
    return finding

messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]
transcript = []
recorded_findings = []
final_obj = None
shell_count = 0
shell_steps = {}
persist({"type": "session_start", "model_label": LABEL})

for turn in range(1, MAX_TURNS + 1):
    raw, timings, finish_reason, usage = call_model(messages)
    model_rec = {"turn": turn, "type": "model", "raw": raw, "timings": timings, "finish_reason": finish_reason, "usage": usage}
    transcript.append(model_rec)
    persist(model_rec)
    obj = parse_json(raw)

    if obj and obj.get("action") == "finding":
        finding = finding_with_evidence(obj, shell_steps)
        recorded_findings.append(finding)
        persist({"type": "finding", "finding": finding})
        messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "Finding saved. Continue according to your role or return action=final."}])
        continue

    if obj and obj.get("action") == "final":
        final_obj = obj
        break

    commands = []
    purpose = ""
    if obj and obj.get("action") == "shell" and obj.get("command"):
        commands = [obj["command"]]
        purpose = obj.get("purpose", "")
    else:
        commands = parse_native_shell_calls(raw)
        if commands:
            purpose = "native model tool call"

    if commands:
        results = []
        for command in commands:
            if shell_count >= MAX_SHELL_STEPS:
                break
            rc, out, secs = run_shell(command)
            shell_count += 1
            rec = {"step": shell_count, "type": "shell", "command": command, "purpose": purpose, "exit_code": rc, "seconds": secs, "output": out}
            shell_steps[shell_count] = rec
            transcript.append(rec)
            persist(rec)
            results.append(f"SHELL_RESULT step={shell_count} exit_code={rc}\n{out[-MODEL_SHELL_OUTPUT_CHARS:]}")
        messages.append({"role": "assistant", "content": raw})
        if results:
            messages.append({"role": "user", "content": "\n\n".join(results)})
        if shell_count >= MAX_SHELL_STEPS:
            break
        continue

    messages.extend([{"role": "assistant", "content": raw}, {"role": "user", "content": "Invalid format. Return action=shell, action=finding, or action=final."}])

if final_obj is None:
    forced = """Stop. Persist one substantiated finding with action=finding if you still have an unrecorded one; otherwise return only {\"action\":\"final\",\"summary\":\"concise summary\"}. Do not use markdown."""
    raw, timings, finish_reason, usage = call_model(messages + [{"role": "user", "content": forced}], max_tokens=FINAL_TOKENS)
    persist({"type": "final_model", "raw": raw, "timings": timings, "finish_reason": finish_reason, "usage": usage})
    obj = parse_json(raw)
    if obj and obj.get("action") == "finding":
        finding = finding_with_evidence(obj, shell_steps)
        recorded_findings.append(finding)
        persist({"type": "finding", "finding": finding, "source": "forced_final"})
        final_obj = {"action": "final", "summary": "Final finding persisted."}
    elif obj and obj.get("action") == "final":
        final_obj = obj
    else:
        final_obj = {"action": "final", "summary": "Model did not produce valid final JSON", "raw": raw}

deduped = []
seen = set()
for finding in recorded_findings:
    key = (finding.get("title", "").strip().lower(), finding.get("claim", "").strip().lower())
    if key in seen:
        continue
    seen.add(key)
    deduped.append(finding)

persist({"type": "session_end", "finding_count": len(deduped), "shell_step_count": shell_count, "summary": final_obj.get("summary", "")})
result = {
    "github_run_id": os.environ.get("GITHUB_RUN_ID"),
    "role": ROLE,
    "worker": WORKER,
    "model": LABEL,
    "slug": MODEL,
    "read_only": READ_ONLY,
    "finding_count": len(deduped),
    "shell_step_count": shell_count,
    "final": final_obj,
    "findings": deduped,
    "transcript": transcript,
}
(OUT / "result.json").write_text(json.dumps(result, indent=2))

lines = [f"# Rails Orchestra — {WORKER}", f"Role: **{ROLE}**", f"Model: **{LABEL}**", "", f"Findings: **{len(deduped)}**", f"Shell steps: **{shell_count}**", "", final_obj.get("summary", ""), ""]
for idx, finding in enumerate(deduped, 1):
    lines += [f"## {idx}. {finding.get('title', 'Untitled')}", f"Severity: {finding.get('severity', '?')}", "", finding.get("claim", ""), "", f"Evidence steps: {finding.get('evidence_steps', [])}", "", finding.get("evidence_summary", ""), "", f"Suggested fix: {finding.get('fix', '')}", ""]
(OUT / "report.md").write_text("\n".join(lines))
print(json.dumps({"worker": WORKER, "role": ROLE, "model": LABEL, "finding_count": len(deduped), "shell_step_count": shell_count}, indent=2))
