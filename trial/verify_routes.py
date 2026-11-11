"""Real upstream streamed tool-call probes; tool calls are inspected, not executed."""
import concurrent.futures
import json
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent

def probe(route):
    name, base, model = route
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Call terminal with command exactly: printf safeagents-route-ok. Do not explain."}],
        "tools": [{"type": "function", "function": {"name": "terminal", "description": "Run a shell command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}],
        "tool_choice": {"type": "function", "function": {"name": "terminal"}},
        "max_tokens": 256,
        "stream": True,
        "temperature": 0,
    }
    calls = {}
    with httpx.Client(timeout=180) as client:
        with client.stream("POST", base + "/chat/completions", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                event = json.loads(line[6:])
                for choice in event.get("choices", []):
                    for call in choice.get("delta", {}).get("tool_calls", []):
                        item = calls.setdefault(call["index"], {"name": "", "arguments": ""})
                        function = call.get("function", {})
                        item["name"] += function.get("name", "")
                        item["arguments"] += function.get("arguments", "")
    assert calls, f"{name}: no tool calls received"
    for item in calls.values():
        assert item["name"] == "terminal", item
        assert json.loads(item["arguments"])["command"] == "printf safeagents-route-ok", item
    result = {"route": name, "base_url": base, "model": model, "streamed_tool_calls": list(calls.values()), "passed": True}
    (ROOT / f"verify-{name}.json").write_text(json.dumps(result, indent=2) + "\n")
    return result

if __name__ == "__main__":
    routes = [("glm", "http://127.0.0.1:8788/api/coding/paas/v4", "glm-5.3"), ("local", "http://127.0.0.1:8789/v1", "mem-llm-large")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(probe, routes):
            print(json.dumps(result), flush=True)
