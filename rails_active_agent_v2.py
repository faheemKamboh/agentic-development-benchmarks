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
MAX_STEPS = 14

SYSTEM = r'''You are an authorized senior Ruby on Rails engineer working inside a disposable isolated benchmark container. You are evaluated on repository-level engineering, not prose.

Read TASK.md first. Use the shell to inspect the repository, reproduce failures, edit files, add migrations, and run tests. Do not modify, delete, skip, or weaken tests. Do not access anything outside the target container and do not attempt network access. Prefer minimal production-minded fixes.

You must return exactly one JSON object per turn.

To run a shell command:
{"action":"shell","command":"...","purpose":"..."}

When finished:
{"action":"final","summary":"...","evidence_steps":[1,2,3],"changes":["..."],"tests":["..."]}

Only cite evidence_steps that correspond to shell commands you actually ran. Do not claim a test passed unless its command output showed success.'''

USER = r'''Work in /work/app. Start by reading TASK.md and checking repository/test state. Solve the task as an autonomous Rails engineer. The benchmark includes hidden regression tests you cannot inspect, so fix the underlying issues rather than tailoring only to the visible failure.'''


def call_model(messages):
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 420,
        "stream": False,
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.load(resp)
    msg = data["choices"][0]["message"]
    text = (msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
    return text, data.get("timings", {})


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


def run_shell(command):
    started = time.time()
    try:
        proc = subprocess.run(
            ["docker", "exec", "-w", "/work/app", "rails-target", "bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=180,
        )
        combined = proc.stdout
        if proc.stderr:
            combined += "\nSTDERR:\n" + proc.stderr
        return proc.returncode, combined[-14000:], round(time.time() - started, 2)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return 124, (stdout + "\nTIMEOUT\n" + stderr)[-14000:], round(time.time() - started, 2)


messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]
transcript = []
final_obj = None
shell_counter = 0

for turn in range(1, MAX_STEPS + 1):
    raw, timings = call_model(messages)
    obj = parse_json(raw)
    transcript.append({"turn": turn, "type": "model", "raw": raw, "timings": timings})

    if not obj:
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": "Invalid format. Return exactly one JSON object with action=shell or action=final."})
        continue

    if obj.get("action") == "final":
        final_obj = obj
        break

    if obj.get("action") != "shell" or not obj.get("command"):
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": "Use action=shell with a command, or action=final."})
        continue

    shell_counter += 1
    rc, output, seconds = run_shell(obj["command"])
    record = {
        "step": shell_counter,
        "type": "shell",
        "command": obj["command"],
        "purpose": obj.get("purpose", ""),
        "exit_code": rc,
        "seconds": seconds,
        "output": output,
    }
    transcript.append(record)
    messages.append({"role": "assistant", "content": raw})
    messages.append({
        "role": "user",
        "content": f"SHELL_RESULT step={shell_counter} exit_code={rc}\n{output}",
    })

if final_obj is None:
    raw, timings = call_model(messages + [{
        "role": "user",
        "content": "Stop now. Return action=final with a concise summary, changes, tests, and only evidence_steps you actually executed.",
    }])
    final_obj = parse_json(raw) or {
        "action": "final",
        "summary": "Model did not produce valid final JSON",
        "evidence_steps": [],
        "changes": [],
        "tests": [],
        "raw": raw,
    }
    transcript.append({"turn": MAX_STEPS + 1, "type": "model-final", "raw": raw, "timings": timings})

valid_steps = {entry["step"] for entry in transcript if entry.get("type") == "shell"}
refs = final_obj.get("evidence_steps") or []
mechanically_evidenced = bool(refs) and all(isinstance(step, int) and step in valid_steps for step in refs)

Path("agent-output").mkdir(exist_ok=True)
result = {
    "model": LABEL,
    "slug": MODEL,
    "mechanically_evidenced_final": mechanically_evidenced,
    "final": final_obj,
    "transcript": transcript,
}
Path("agent-output/result.json").write_text(json.dumps(result, indent=2))

lines = [
    f"# Rails active-agent v2 — {LABEL}",
    "",
    f"Mechanically evidenced final: **{mechanically_evidenced}**",
    "",
    final_obj.get("summary", ""),
    "",
    "## Claimed changes",
]
for change in final_obj.get("changes", []):
    lines.append(f"- {change}")
lines += ["", "## Claimed tests"]
for test in final_obj.get("tests", []):
    lines.append(f"- {test}")
lines += ["", "# Transcript", ""]
for entry in transcript:
    if entry.get("type") == "shell":
        lines += [
            f"## Shell step {entry['step']}",
            f"Purpose: {entry.get('purpose', '')}",
            "",
            f"`{entry['command']}`",
            f"exit={entry['exit_code']} time={entry['seconds']}s",
            "",
            "```text",
            entry["output"],
            "```",
            "",
        ]
Path("agent-output/report.md").write_text("\n".join(lines))
print(json.dumps({"model": LABEL, "mechanically_evidenced_final": mechanically_evidenced, "final": final_obj}, indent=2))
