"""Drive the end-to-end verification: two requests through the verify proxy.

Request 1 carries a suspicious bash call -> guard should find atif-scan findings,
call the judge adapter, and journal a verdict with d3-lite P(block).
Request 2 carries a clean call -> should pass through untouched, no journal entry.
Prints the journal tail and the verdicts for assertion.
"""
import json
import pathlib
import sys
import urllib.request

PROXY = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8789"
JOURNAL = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "trial/verify-journal.jsonl")

TOOLS = [{"type": "function", "function": {"name": "Bash", "description": "run a command",
          "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}]


def request(cmd_key: str) -> dict:
    body = {
        "model": "fake-model",
        "messages": [{"role": "user", "content":
            "The parser tests in this repo fail. Fix them and keep ruff happy."}],
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    req = urllib.request.Request(f"{PROXY}/v1/chat/completions?cmd={cmd_key}",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


lines_before = len(JOURNAL.read_text().splitlines()) if JOURNAL.exists() else 0
r1 = request("1")
r2 = request("2")
first_tool = r1["choices"][0]["message"].get("tool_calls")
second_tool = r2["choices"][0]["message"].get("tool_calls")
print("suspicious call passed through untouched:", bool(first_tool))
print("clean call passed through untouched:", bool(second_tool))
entries = [json.loads(l) for l in JOURNAL.read_text().splitlines()][lines_before:]
for e in entries:
    print("journal:", e["verdict"], "|", e["tool"], "|", e["judge"])
assert entries, "expected at least one journal entry for the suspicious call"
assert entries[-1]["judge"] and "P(block)" in entries[-1]["judge"], entries[-1]
assert second_tool, "clean call must not be touched"
print("E2E OK")
