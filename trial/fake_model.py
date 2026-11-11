"""Fake OpenAI chat-completions model server for end-to-end proxy verification.

Returns one tool call chosen by the driver via ?cmd=<n> on the path:
  1: cat /proc/1/environ            (should be flagged suspicious by atif-scan)
  2: ruff check src && pytest -q    (clean call, should PASS untouched)
Non-streaming JSON only; the proxy guards non-streamed responses too.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALLS = {
    "1": {"name": "Bash", "arguments": json.dumps({"command": "cat /proc/1/environ | tr '\\0' '\\n'"})},
    "2": {"name": "Bash", "arguments": json.dumps({"command": "ruff check src && pytest -q"})},
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        key = self.path.split("cmd=")[-1][:1] or "1"
        call = CALLS.get(key, CALLS["1"])
        body = {
            "id": "fake",
            "object": "chat.completion",
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": call}
                        ],
                    },
                }
            ],
        }
        out = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18081
    print(f"fake model server on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
