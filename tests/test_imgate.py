"""Offline tests with the real `openai` library against a localhost mock that imitates
OpenRouter (/api/v1/images) and OpenAI-style servers (/v1/images/generations, /edits, /models)."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "imgate" / "imgate.py"
PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")).decode()
KEY = "sk-or-test-key-123456"
SEEN = []  # (path, headers, body-bytes)
FAIL_ONCE = {"left": 1}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        SEEN.append((self.path, {k.lower(): v for k, v in self.headers.items()}, b""))
        if self.path == "/api/v1/images/models":
            return self._send(200, {"data": [{"id": "black-forest-labs/flux.2-pro", "name": "Flux 2 Pro",
                                              "supported_parameters": {"aspect_ratio": {"type": "enum"}}}]})
        if self.path == "/v1/models":
            return self._send(200, {"object": "list", "data": [{"id": "Qwen/Qwen-Image", "object": "model",
                                                                "created": 0, "owned_by": "vllm"}]})
        self._send(404, {"error": {"message": "nope"}})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        SEEN.append((self.path, {k.lower(): v for k, v in self.headers.items()}, raw))
        if self.path.startswith("/noimg/"):  # Ollama (Gin) answers a missing route with plain text
            b = b"404 page not found"
            self.send_response(404); self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); return self.wfile.write(b)
        if self.path in ("/api/v1/images", "/v1/images/generations"):
            body = json.loads(raw)
            if self.path == "/api/v1/images":  # live OpenRouter (2026-11-22): entries must be objects, not strings
                for field in ("input_references", "markup"):
                    entries = body.get(field)
                    entries = [entries] if field == "markup" and entries is not None else entries or []
                    for i, e in enumerate(entries):
                        if not isinstance(e, dict):
                            where = ["input_references", i] if field == "input_references" else ["markup"]
                            issue = {"expected": "object", "code": "invalid_type", "path": where,
                                     "message": "Invalid input: expected object, received string"}
                            return self._send(400, {"error": {"message": json.dumps([issue])}})
            if body["prompt"].startswith("FAIL502") and FAIL_ONCE["left"]:
                FAIL_ONCE["left"] -= 1
                return self._send(502, {"error": {"message": "upstream failed"}})
            if body["prompt"].startswith("BAD"):
                return self._send(400, {"error": {"message": f"unsupported parameter {KEY}"}})
            item = {"url": f"http://127.0.0.1:{self.server.server_port}/img.png"} if body.get("response_format") == "url" \
                else {"b64_json": PNG}
            extra = {"usage": {"cost": 0.04}} if self.path.startswith("/api") else {}
            return self._send(200, {"created": 1, "data": [item] * body.get("n", 1), **extra})
        if self.path == "/v1/images/edits":
            return self._send(200, {"created": 1, "data": [{"b64_json": PNG}]})
        self._send(404, {"error": {"message": "nope"}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_port
# serve /img.png for response_format=url
orig_get = H.do_GET
def do_get(self):
    if self.path == "/img.png":
        b = base64.b64decode(PNG); self.send_response(200); self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); return self.wfile.write(b)
    orig_get(self)
H.do_GET = do_get

tmp = Path(tempfile.mkdtemp())
cfgp = tmp / "c.toml"
cfgp.write_text(f'''output_dir = "{tmp}/out"
timeout = 20
[profiles.openrouter]
style = "openrouter"
base_url = "http://127.0.0.1:{PORT}/api/v1"
api_key_env = "OPENROUTER_API_KEY"
model = "black-forest-labs/flux.2-pro"
[profiles.vllm]
style = "openai"
base_url = "http://127.0.0.1:{PORT}/v1"
model = "Qwen/Qwen-Image"
[profiles.vllm.extra]
num_inference_steps = 20
[profiles.or2]
style = "openrouter"
base_url = "http://127.0.0.1:{PORT}/api/v1"
api_key_env = "OPENROUTER_API_KEY"
model = "black-forest-labs/flux.2-pro"
ref_shape = "image_url"
[profiles.orstr]
style = "openrouter"
base_url = "http://127.0.0.1:{PORT}/api/v1"
api_key_env = "OPENROUTER_API_KEY"
model = "black-forest-labs/flux.2-pro"
ref_shape = "string"
[profiles.noimg]
style = "openai"
base_url = "http://127.0.0.1:{PORT}/noimg/v1"
model = "x"
[profiles.lan]
style = "openai"
base_url = "http://192.0.2.1:9/v1"
api_key_env = "LAN_KEY"
model = "m"
[mcp]
allowed_models = ["black-forest-labs/flux.2-pro", "Qwen/Qwen-Image", "m"]
max_n = 2
[presets.cin]
prompt_suffix = ", cinematic"
[presets.cin.params]
aspect_ratio = "16:9"
[presets.local]
profile = "vllm"
''')
ENV = {**os.environ, "OPENROUTER_API_KEY": KEY, "IMGATE_CONFIG": str(cfgp), "LAN_KEY": "lan-secret-key-999",
       "OPENAI_BASE_URL": "https://should-be-ignored.invalid/v1"}


def run(*args, inp=None, env=ENV):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, input=inp, env=env, check=False)

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: ok.bad += 1
ok.bad = 0
def last(path_suffix):
    return [s for s in SEEN if s[0] == path_suffix][-1]

ref = tmp / "ref.png"; ref.write_bytes(base64.b64decode(PNG))

# ---- OpenRouter style (native /images through the openai client)
r = run("generate", "a manger", "-p", "cin", "--seed", "7", "--ref", str(ref), "--zdr", "--subdir", "Test Dir")
ok(r.returncode == 0, f"OR generate exit 0 ({r.stderr.strip()[:200]})")
p = Path(r.stdout.strip().splitlines()[0]) if r.stdout.strip() else None
ok(p and p.read_bytes()[:4] == b"\x89PNG", "png saved")
ok(p and Path(str(p) + ".json").is_file() and "test-dir" in str(p), "sidecar + slugified subdir")
path, hdr, raw = last("/api/v1/images"); b = json.loads(raw)
ok(hdr.get("authorization") == f"Bearer {KEY}" and hdr.get("x-title") == "imgate", "OR auth + X-Title headers")
ok(b["prompt"] == "a manger, cinematic" and b["aspect_ratio"] == "16:9" and b["seed"] == 7, "preset+seed merged")
ok(b["provider"] == {"zdr": True} and b["input_references"][0]["image_url"]["url"].startswith("data:image/png;base64,"), "provider + local ref -> data url in object entry")
ok(b["input_references"][0]["type"] == "image_url", "default ref shape is the object form OpenRouter requires")
ok("organization" not in set(hdr) and "openai-organization" not in set(hdr), "no org header leak")
side = Path(str(p) + ".json").read_text()
ok(KEY not in side and "base64" not in side and "ref.png" in side, "sidecar: no key, no image bytes")
r = run("models", "-v"); ok("flux.2-pro" in r.stdout and "aspect_ratio" in r.stdout, "OR models list")
r = run("generate", "FAIL502 retry me"); ok(r.returncode == 0, f"502 retried by openai client ({r.stderr.strip()[:100]})")
r = run("generate", "BAD thing"); ok(r.returncode == 1 and KEY not in r.stderr and "unsupported parameter" in r.stderr, "400 surfaced, key redacted")
r = run("generate", "x", env={**ENV, "OPENROUTER_API_KEY": ""}); ok(r.returncode == 1 and "OPENROUTER_API_KEY" in r.stderr, "missing key message")

# ---- OpenAI style (vLLM-Omni / Ollama shape)
r = run("generate", "a cat", "-P", "vllm", "--size", "512x512", "--seed", "42", "--aspect-ratio", "16:9",
        "-n", "2", "--set", "num_inference_steps=30", "--set", "guidance_scale=4.5")
ok(r.returncode == 0 and len(r.stdout.split()) == 2, f"vllm generate n=2 ({r.stderr.strip()[:200]})")
path, hdr, raw = last("/v1/images/generations"); b = json.loads(raw)
ok(hdr.get("authorization") == "Bearer none", "no key -> placeholder bearer")
ok(b["model"] == "Qwen/Qwen-Image" and b["size"] == "512x512" and b["n"] == 2, "native params")
ok(b["seed"] == 42 and b["aspect_ratio"] == "16:9", "non-SDK params routed to body via extra_body")
ok(b["num_inference_steps"] == 30 and b["guidance_scale"] == 4.5, "--set overrides profile extra, JSON-typed")
ok("x-title" not in hdr, "no OpenRouter headers on openai style")
r = run("generate", "a cat", "-P", "vllm", "--response-format", "url"); ok(r.returncode == 0 and Path(r.stdout.strip()).read_bytes()[:4] == b"\x89PNG", "url response downloaded (same host http ok)")
r = run("generate", "edit it", "-p", "local", "--ref", str(ref), "--seed", "3")
path, hdr, raw = last("/v1/images/edits")
ok(r.returncode == 0 and "multipart/form-data" in hdr.get("content-type", "") and b'name="image"' in raw and b'name="seed"' in raw, f"edits via multipart ({r.stderr.strip()[:200]})")
r = run("generate", "a cat", "-P", "noimg")
ok(r.returncode == 1 and "/noimg/v1/images/generations does not exist" in r.stderr and "Error code: 404" not in r.stderr, "plain-text 404 explains the missing images route")
r = run("generate", "a cat", "-P", "noimg", "--ref", str(ref))
ok(r.returncode == 1 and "/images/edits does not exist" in r.stderr, "same hint for the edits route")
r = run("init", "--print"); ok("[profiles.ollama]" not in r.stdout.replace("# [profiles.ollama]", ""), "example config no longer enables the Ollama profile")
r = run("models", "-P", "vllm"); ok("Qwen/Qwen-Image" in r.stdout, "openai-style models list")
r = run("generate", "x", "-P", "vllm", "--zdr"); ok(r.returncode == 1 and "style=openrouter" in r.stderr, "provider routing rejected on openai style")
r = run("generate", "x", "-P", "lan"); ok(r.returncode == 1 and "plain http" in r.stderr and "lan-secret" not in r.stderr, "key over non-loopback http refused")
r = run("generate", "x", "-P", "nope"); ok(r.returncode == 1 and "Unknown profile" in r.stderr, "unknown profile")
r = run("profiles"); ok(json.loads(r.stdout)[0]["name"] == "openrouter" and KEY not in r.stdout, "profiles list, no secrets")

# ---- misc
r = run("generate", "x", "--dry-run", "-P", "vllm", "--ref", str(ref)); d = json.loads(r.stdout)["request"]
ok(d["endpoint"] == "POST /images/edits" and d["body"]["num_inference_steps"] == 20, "dry-run view (openai/edits)")
n = len(SEEN); r = run("generate", "", "--dry-run"); ok(r.returncode == 1 and "Empty prompt" in r.stderr, "empty prompt rejected")
r = run("generate", "--prompt-file", "-", "--dry-run", inp="from stdin"); ok(json.loads(r.stdout)["request"]["body"]["prompt"] == "from stdin", "stdin prompt")
r = subprocess.run([sys.executable, "-c", f"import sys,runpy; sys.modules['openai']=None; sys.argv=['imgate','models']; runpy.run_path(r'{SCRIPT}', run_name='__main__')"],
                   capture_output=True, text=True, env=ENV, check=False)
ok("openai` package is not installed" in r.stderr, "friendly message when openai is missing")
ok("requires-python = \">=3.12\"" in SCRIPT.read_text() and "sys.version_info < (3, 12)" in SCRIPT.read_text(), "3.12 gate present")

# ---- lint, mask, ref_shape, batch
import struct
import zlib


def mkpng(w, h):
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")
mask1, mask2 = tmp / "mask1.png", tmp / "mask2.png"
mask1.write_bytes(mkpng(1, 1)); mask2.write_bytes(mkpng(2, 2))

r = run("generate", "one Bible, no second book, without a lid", "--dry-run")
ok("warning: Negation" in r.stderr and "no second book" in r.stderr, "lint warns on negation")
ok(json.loads(r.stdout)["warnings"], "lint warnings in JSON output")
r = run("generate", 'a sign reading "NO ENTRY" on a gate', "--dry-run"); ok("warning" not in r.stderr, "lint ignores quoted verbatim text")
r = run("generate", "no second book", "--dry-run", "--no-lint"); ok("warning" not in r.stderr, "--no-lint silences")
r = run("generate", "a calm lake at dawn", "--dry-run"); ok("warning" not in r.stderr, "no false positive on clean prompt")

r = run("generate", "edit", "--ref", str(ref), "--mask", str(mask1))
ok(r.returncode == 0, f"OR mask generate ({r.stderr.strip()[:200]})")
b = json.loads(last("/api/v1/images")[2]); ok(b["markup"]["image_url"]["url"].startswith("data:image/png;base64,") and isinstance(b["input_references"][0], dict), "OR mask -> markup (object shape, default)")
r = run("generate", "edit", "-P", "or2", "--ref", str(ref), "--mask", str(mask1))
b = json.loads(last("/api/v1/images")[2])
ok(b["input_references"][0]["image_url"]["url"].startswith("data:") and b["markup"]["type"] == "image_url", "ref_shape=image_url wraps refs and markup")
r = run("generate", "edit", "-P", "orstr", "--ref", str(ref))
ok(r.returncode == 1 and "expected object, received string" in r.stderr, "ref_shape=string opt-in reproduces the live HTTP 400")
r = run("generate", "edit", "-P", "orstr", "--ref", str(ref), "--mask", str(mask1), "--dry-run")
d = json.loads(r.stdout)["request"]["body"]
ok(isinstance(d["input_references"][0], str) and isinstance(d["markup"], str), "dry-run mirrors ref_shape=string")
r = run("generate", "edit", "--ref", str(ref), "--mask", str(mask1), "--dry-run")
d = json.loads(r.stdout)["request"]["body"]
ok(d["input_references"][0]["type"] == "image_url" and d["markup"]["type"] == "image_url" and d["markup"]["image_url"]["url"].endswith("mask1.png"), "dry-run shows object entries for refs and markup")
(tmp / "badshape.toml").write_text('[profiles.openrouter]\nstyle = "openrouter"\nbase_url = "http://x/v1"\nref_shape = "nope"\n')
r = run("--config", str(tmp / "badshape.toml"), "generate", "x", "--dry-run")
ok(r.returncode == 1 and "ref_shape must be" in r.stderr, "invalid ref_shape rejected")
r = run("generate", "edit", "-P", "vllm", "--ref", str(ref), "--mask", str(mask1))
ok(r.returncode == 0 and b'name="mask"' in last("/v1/images/edits")[2], f"openai-style mask multipart ({r.stderr.strip()[:150]})")
r = run("generate", "edit", "--ref", str(ref), "--mask", str(mask2)); ok(r.returncode == 1 and "must equal" in r.stderr, "mask size mismatch rejected")
r = run("generate", "edit", "--mask", str(mask1)); ok(r.returncode == 1 and "needs at least one reference" in r.stderr, "mask without ref rejected")
(tmp / "notpng.png").write_bytes(b"GIF89a....")
r = run("generate", "edit", "--ref", str(ref), "--mask", str(tmp / "notpng.png")); ok(r.returncode == 1 and "PNG" in r.stderr, "non-PNG mask rejected")

jobs = tmp / "jobs.jsonl"
jobs.write_text("\n".join([
    "# comment line",
    json.dumps({"prompt": "job one", "seed": 1, "aspect_ratio": "16:9"}),
    json.dumps({"prompt": "job two", "profile": "vllm", "set": {"num_inference_steps": 5}, "size": "256x256"}),
    json.dumps({"prompt": "job three", "bogus_field": 1}),
    json.dumps({"prompt": "job four", "preset": "cin", "subdir": "b-dir", "name": "custom"}),
]) + "\n")
before = len(SEEN)
r = run("batch", str(jobs), "--concurrency", "2", "--subdir", "batch-run")
res = [json.loads(l) for l in r.stdout.splitlines()]
ok(len(res) == 4 and [x["job"] for x in res] == [1, 2, 3, 4], "batch: 4 results in order")
ok(res[0]["ok"] and res[1]["ok"] and res[3]["ok"] and not res[2]["ok"] and "Unknown job field" in res[2]["error"], "batch: one bad job does not stop others")
ok(r.returncode == 1 and "3/4 jobs ok" in r.stderr, "batch: exit 1 and summary")
ok(res[1]["profile"] == "vllm" and Path(res[0]["files"][0]["path"]).parent.name == "batch-run" and Path(res[3]["files"][0]["path"]).parent.name == "b-dir", "batch: per-job profile and subdir")
ok(len(SEEN) - before == 3, "batch: bad job made no API call")
r = run("batch", str(jobs), "--dry-run", "--max-jobs", "2"); ok(r.returncode == 1 and "exceeds the limit" in r.stderr, "batch: max-jobs guard")
r = run("batch", str(jobs), "--dry-run"); ok(len([l for l in r.stdout.splitlines()]) == 4 and len(SEEN) - before == 3, "batch dry-run: no calls")

# ---- check / guide
r = run("check", "a dog"); ok("Very short prompt" in r.stdout and "No style" in r.stdout, "check: short + no style hints")
r = run("check", 'A cinematic wide shot of a stable at dusk with a sign reading "OPEN LATE"'); ok("hint" not in r.stdout and "No issues" in r.stdout, "check: complete prompt is clean")
r = run("check", "A cinematic wide shot of a stable at dusk with a sign that says open late"); ok("without the exact wording in quotes" in r.stdout, "check: unquoted text hint")
r = run("check", "one Bible, no second book, photo, wide shot, candlelight", "--json"); j = json.loads(r.stdout); ok(j["warnings"] and "note" in j, "check --json has warnings + heuristic note")
r = run("check", ""); ok(r.returncode == 1, "check: empty prompt rejected")
r = run("guide", "--list"); ok("Negations and exclusions" in r.stdout and "Model-specific notes" in r.stdout, "guide --list")
r = run("guide", "--section", "negation"); ok("Rewrite positively" in r.stdout and "Quick rules" not in r.stdout, "guide --section")
r = run("guide", "--section", "zzz"); ok(r.returncode == 1 and "Sections:" in r.stderr, "guide: unknown section error")

# ---- MCP
def mcp(msgs):
    r = run("serve", inp="\n".join(json.dumps(m) for m in msgs) + "\n")
    return [json.loads(l) for l in r.stdout.splitlines()], r.stderr
call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
out, err = mcp([
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    call(3, "generate_image", {"prompt": "mcp pic", "aspect_ratio": "16:9", "n": 2}),
    call(4, "generate_image", {"prompt": "x", "model": "other/model"}),
    call(5, "generate_image", {"prompt": "x", "n": 9}),
    call(6, "generate_image", {"prompt": "x", "references": ["/etc/passwd"]}),
    {"jsonrpc": "2.0", "id": 7, "method": "nope"},
    call(8, "generate_image", {"prompt": "local one", "profile": "vllm", "seed": 1}),
    call(9, "list_profiles", {}),
    call(10, "generate_image", {"prompt": "x", "profile": "lan"}),
    call(11, "generate_image", {"prompt": "one book, no second book"}),
    call(12, "generate_image", {"prompt": "x", "references": [str(ref)], "mask": str(mask1)}),
    call(13, "generate_image", {"prompt": "x", "references": [str(ref)], "mask": "/etc/hosts"}),
    call(14, "check_prompt", {"prompt": "a dog, no collar"}),
    call(15, "prompt_guide", {"section": "edits"}),
    call(16, "prompt_guide", {"section": "nope"}),
])
by = {o["id"]: o for o in out}
ok(len(out) == 16, f"16 responses, none for the notification (got {len(out)})")
ok(by[1]["result"]["protocolVersion"] == "2025-06-18" and "check_prompt" in by[1]["result"]["instructions"], "initialize + instructions")
ok([t["name"] for t in by[2]["result"]["tools"]] == ["generate_image", "list_image_models", "check_prompt", "prompt_guide", "list_profiles", "list_presets"], "tools/list")
c = by[3]["result"]["content"]
ok(not by[3]["result"].get("isError") and sum(x["type"] == "image" for x in c) == 2 and json.loads(c[0]["text"])["cost_usd"] == 0.04, "OR generate: 2 inline images + cost")
ok(by[4]["result"]["isError"] and "allowed_models" in by[4]["result"]["content"][0]["text"], "model guard")
ok(by[5]["result"]["isError"] and "max_n" in by[5]["result"]["content"][0]["text"], "max_n guard")
ok(by[6]["result"]["isError"] and "not allowed" in by[6]["result"]["content"][0]["text"], "reference path guard")
ok(by[7]["error"]["code"] == -32601, "unknown method error")
ok(not by[8]["result"].get("isError") and json.loads(by[8]["result"]["content"][0]["text"])["profile"] == "vllm", "MCP with local profile")
ok(KEY not in json.dumps(by[9]) and "lan" in json.dumps(by[9]), "list_profiles, no secrets")
ok(by[10]["result"]["isError"] and "plain http" in by[10]["result"]["content"][0]["text"], "MCP surfaces key-over-http refusal")
ok("warnings" in json.loads(by[11]["result"]["content"][0]["text"]), "MCP surfaces lint warnings")
ok(by[12]["result"].get("isError") and "not allowed" in by[12]["result"]["content"][0]["text"], "MCP reference outside allowed dirs refused (tmp is not output_dir)")
ok(by[13]["result"]["isError"] and "not allowed" in by[13]["result"]["content"][0]["text"], "MCP mask path guard")
ok("hints" in by[14]["result"]["content"][0]["text"] and "Negation" in by[14]["result"]["content"][0]["text"], "MCP check_prompt")
ok("pixel-identical" in by[15]["result"]["content"][0]["text"], "MCP prompt_guide section")
ok(by[16]["result"]["isError"], "MCP prompt_guide bad section is an error")
ok(err == "", f"nothing on stderr ({err[:100]})")
print("FAILED" if ok.bad else "ALL PASSED", ok.bad)
sys.exit(1 if ok.bad else 0)
