#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai>=1.50"]
# ///
"""imgate: image generation via OpenRouter or any OpenAI-compatible server (CLI + MCP stdio server).

Python 3.12+. The only third-party dependency is the `openai` client library.

  imgate.py generate "prompt" [-P profile] [-m model] [-p preset] [--set key=value] ...
  imgate.py models [-P profile] | profiles | presets | config | init
  imgate.py serve        # MCP server over stdio (Claude Desktop / Claude Code)

Profiles (config.toml) pick the endpoint and API style:
  style = "openrouter"  POST {base_url}/images, GET {base_url}/images/models   (OpenRouter native)
  style = "openai"      POST {base_url}/images/generations and /images/edits   (OpenAI API, vLLM-Omni,
                        Ollama experimental, and other OpenAI-compatible servers)
API keys are read from environment variables named by the profile, never from the config file.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 (friendly message when run by an old python3)
    sys.exit(f"imgate requires Python 3.12 or newer (this is {sys.version.split()[0]}).")

import argparse
import base64
import copy
import datetime as dt
import inspect
import json
import mimetypes
import os
import re
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

__version__ = "0.4.0"

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
DEFAULTS: dict = {
    "default_profile": "openrouter",
    "output_dir": "~/imgate-output",
    "timeout": 300,
    # openai-library retries (429, 5xx, connection errors, timeouts). A retry after a timeout can
    # be billed twice on paid endpoints, so keep this low.
    "max_retries": 1,
    "app_title": "imgate",
    "app_url": "",
    "lint_prompt": True,  # warn about negations ("no X", "without Y") in prompts
    "max_batch": 20,      # max jobs per `batch` run (cost guard)
    "defaults": {},  # request params sent on every call (all profiles)
    "provider": {},  # OpenRouter provider routing (style=openrouter only)
    "profiles": {
        "openrouter": {
            "style": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
            "model": "black-forest-labs/flux.2-pro",
        },
    },
    "mcp": {
        "inline_images": True,
        "max_inline_bytes": 1_000_000,
        "allowed_profiles": [],  # empty = any configured profile
        "allowed_models": [],    # empty = any model; set a list as a cost guard
        "max_n": 4,
        "reference_dirs": [],    # extra dirs the MCP tool may read reference images from
    },
    "presets": {},
}

EXAMPLE_CONFIG = '''# imgate config. Lookup: $IMGATE_CONFIG, ./imgate.toml, ~/.config/imgate/config.toml
# API keys are NEVER stored here. A profile names the environment variable that holds its key.

default_profile = "openrouter"      # or set IMGATE_PROFILE / use -P
output_dir      = "~/imgate-output"
timeout         = 300
max_retries     = 1
app_title       = "imgate"           # X-Title header (OpenRouter app attribution)
app_url         = ""                # HTTP-Referer header if set
lint_prompt     = true              # warn when a prompt contains negations (models may render the named thing)
max_batch       = 20                # max jobs per `batch` run

[defaults]                          # sent on every request, all profiles
# seed = 42

[provider]                          # OpenRouter provider routing (style = "openrouter" only)
# allow_fallbacks = false
# data_collection = "deny"
# zdr = true

# ---------------------------------------------------------------- profiles
[profiles.openrouter]
style       = "openrouter"
base_url    = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
model       = "black-forest-labs/flux.2-pro"   # verify with: imgate.py models
# ref_shape = "image_url"   # default: {"type":"image_url","image_url":{"url":...}} per OpenRouter docs.
#                           # "string" sends bare URLs, which live OpenRouter rejects with HTTP 400.

# Self-hosted vLLM-Omni: `vllm serve Qwen/Qwen-Image --omni --port 8000` (one model per server)
[profiles.vllm-omni]
style    = "openai"
base_url = "http://localhost:8000/v1"
model    = "Qwen/Qwen-Image"
# api_key_env = "VLLM_API_KEY"      # only if the server was started with --api-key
# [profiles.vllm-omni.extra]        # sent on every request (provider-specific fields)
# num_inference_steps = 30

# Ollama: NOT usable for image generation through imgate. Its OpenAI-compatible API has no
# /v1/images/generations or /v1/images/edits route (checked live on Ollama 0.40.17, 2026-11-22: both
# return 404). Kept as a comment so the profile can be enabled if a future Ollama adds the routes.
# [profiles.ollama]
# style    = "openai"
# base_url = "http://localhost:11434/v1"
# model    = "x/z-image-turbo"      # registry name; macOS only; unverified against an images route

# OpenAI itself
# [profiles.openai]
# style       = "openai"
# base_url    = "https://api.openai.com/v1"
# api_key_env = "OPENAI_API_KEY"
# model       = "gpt-image-1"

# A key is only ever sent over https, or over http to localhost. For a LAN server that needs a
# key over plain http, set allow_insecure = true on that profile (you accept the exposure).

[mcp]
inline_images    = true
max_inline_bytes = 1000000
allowed_profiles = []               # e.g. ["openrouter", "vllm-omni"]
allowed_models   = []               # e.g. ["black-forest-labs/flux.2-pro"] as a cost guard
max_n            = 4
reference_dirs   = []               # dirs the MCP tool may read reference images from

# Presets: named bundles. Use with -p NAME (CLI) or "preset" (MCP). A preset may pin a profile.
[presets.cinematic]
prompt_suffix = ", cinematic lighting, shallow depth of field, photorealistic"
[presets.cinematic.params]
aspect_ratio = "16:9"               # style=openai servers receive this as an extra field

[presets.local-draft]
profile = "vllm-omni"
[presets.local-draft.params]
size = "1024x1024"
'''

# Parameters with first-class flags. For style=openai, anything the installed openai SDK does not
# declare for the endpoint (aspect_ratio, resolution, seed, ...) is sent in the request body as an
# extra field automatically. Use --set / "extra" for anything not in this list.
PARAM_KEYS = (
    "n", "size", "quality", "response_format", "output_format", "background",
    "output_compression", "resolution", "aspect_ratio", "seed", "frame_count", "frame_duration_ms",
)
_NEG = re.compile(r"\b(no|not|without|never|don't|dont|do not|avoid|avoiding|exclude|excluding|nothing|none)\b[^.,;\n]{0,40}", re.IGNORECASE)


def lint_prompt(prompt: str) -> list[str]:
    """Heuristic warnings. Quoted text (verbatim text to render) is ignored. Never blocks."""
    stripped = re.sub(r'"[^"]*"|\u201c[^\u201d]*\u201d', " ", prompt)
    frags = [m.group(0).strip() for m in _NEG.finditer(stripped)][:4]
    if not frags:
        return []
    shown = "; ".join(f'"{f}"' for f in frags)
    return [(f"Negation in prompt ({shown}). Image models may render the thing you name instead of "
             "omitting it. Prefer saying what should be there (e.g. 'a single open book, alone'). Some "
             "models accept --set negative_prompt=... but others (e.g. FLUX.2) do not; check the model docs.")]


_STYLE = re.compile(r"\b(photo\w*|illustrat\w*|paint\w*|watercolou?r|oil|render\w*|3d|vector|sketch\w*|cinematic|anime|cartoon|pixel|diagram|infographic|logo|mockup|poster|film|35mm|realistic|stylized)\b", re.IGNORECASE)
_FRAMING = re.compile(r"\b(close-?up|wide|macro|aerial|overhead|low-angle|high-angle|eye-level|portrait|shot|lens|\d+mm|framing|centered|foreground|background|composition|aspect|16:9|9:16|1:1|4:3)\b", re.IGNORECASE)
_LIGHT = re.compile(r"\b(light\w*|lit|sunlight|sunset|sunrise|golden hour|candle\w*|neon|shadows?|backlit|glow\w*|overcast|studio|dusk|dawn|night|daylight)\b", re.IGNORECASE)
_TEXTY = re.compile(r"\b(text|sign|signage|headline|title|caption|label|words?|lettering|banner|slogan)\b", re.IGNORECASE)


def check_prompt(prompt: str) -> dict:
    """Heuristic prompt review: negation warnings plus missing-information hints. Never authoritative."""
    words = len(prompt.split())
    hints = []
    if words < 8:
        hints.append("Very short prompt: expect generic results. Add subject details, setting, style, framing and light.")
    if not _STYLE.search(prompt):
        hints.append("No style or medium stated (photograph, watercolor, 3D render, flat vector, ...).")
    if words >= 8 and not _FRAMING.search(prompt):
        hints.append("No framing or composition stated (close-up, wide shot, centered, aspect ratio).")
    if words >= 8 and not _LIGHT.search(prompt):
        hints.append("No lighting or time of day stated.")
    if _TEXTY.search(re.sub(r'"[^"]*"', "", prompt)) and not re.search(r'"[^"]+"|\u201c[^\u201d]+\u201d', prompt):
        hints.append("Text or signage is mentioned without the exact wording in quotes. Quote any text that must appear.")
    return {"words": words, "warnings": lint_prompt(prompt), "hints": hints,
            "note": "Heuristic keyword checks. A missing item is a prompt to consider, not an error."}


FALLBACK_GUIDE = """# Prompting guide (short fallback; references/prompting.md was not found)
1. Describe the scene in plain sentences: subject, setting, style or medium, framing, light.
2. Say what you want, not what you do not want ("an empty street", not "no cars").
3. Put text that must appear in quotes with its placement and style.
4. For edits, say what changes and what must stay identical; one change per pass.
5. Label each reference image's role. Draft cheap first, then spend on the final.
6. Change one thing at a time and keep the same seed."""


def load_guide() -> str:
    try:
        return (Path(__file__).resolve().parent / "references" / "prompting.md").read_text()
    except OSError:
        return FALLBACK_GUIDE


def guide_sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^## ", text)
    return [(blk.split("\n", 1)[0].strip(), "## " + blk) for blk in parts[1:]]


def get_guide(section: str | None = None) -> str:
    text = load_guide()
    if not section:
        return text
    secs = guide_sections(text)
    hit = [body for title, body in secs if section.lower() in title.lower()]
    if not hit:
        raise ImgateError(f"No section matching '{section}'. Sections: {', '.join(t for t, _ in secs) or 'none'}")
    return "\n".join(hit)


def png_size(b: bytes) -> tuple[int, int] | None:
    if b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR":
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    return None


MAX_REF_BYTES = 20 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_SECRETS: set[str] = set()


class ImgateError(Exception):
    pass


# ----------------------------------------------------------------- config

def deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def config_candidates() -> list[Path]:
    out = []
    if os.environ.get("IMGATE_CONFIG"):
        out.append(Path(os.environ["IMGATE_CONFIG"]).expanduser())
    out += [Path.cwd() / "imgate.toml", Path("~/.config/imgate/config.toml").expanduser()]
    return out


def load_config(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    for p in ([Path(path).expanduser()] if path else config_candidates()):
        if p.is_file():
            try:
                with p.open("rb") as fh:
                    deep_merge(cfg, tomllib.load(fh))
            except Exception as e:
                raise ImgateError(f"Bad config {p}: {e}") from e
            cfg["_path"] = str(p)
            break
    for name, prof in cfg["profiles"].items():
        if prof.get("style") not in ("openrouter", "openai"):
            raise ImgateError(f"Profile '{name}': style must be 'openrouter' or 'openai'.")
        if not prof.get("base_url"):
            raise ImgateError(f"Profile '{name}': base_url is required.")
        if prof.get("ref_shape", "image_url") not in ("image_url", "string"):
            raise ImgateError(f"Profile '{name}': ref_shape must be 'image_url' or 'string'.")
    return cfg


def get_profile(cfg: dict, name: str | None) -> dict:
    name = name or os.environ.get("IMGATE_PROFILE") or cfg["default_profile"]
    if name not in cfg["profiles"]:
        raise ImgateError(f"Unknown profile '{name}'. Available: {', '.join(cfg['profiles'])}")
    prof = copy.deepcopy(cfg["profiles"][name])
    prof["name"] = name
    return prof


def resolve_key(prof: dict) -> str:
    env = prof.get("api_key_env") or ""
    val = os.environ.get(env, "").strip() if env else ""
    required = prof.get("require_key", prof["style"] == "openrouter")
    if required and not val:
        raise ImgateError(
            f"{env or 'An API key variable'} is not set (profile '{prof['name']}'). Export it in the "
            "environment that launches this tool (for Claude Desktop, use the MCP server's `env` block)."
        )
    if val:
        _SECRETS.add(val)
    return val


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", text)


# ----------------------------------------------------------------- client

def make_client(cfg: dict, prof: dict):
    try:
        import openai
    except ModuleNotFoundError:
        raise ImgateError(
            "The `openai` package is not installed for this Python. Run: "
            f"{sys.executable} -m pip install openai   (or run the script with `uv run`)."
        ) from None
    key = resolve_key(prof)
    u = urlparse(prof["base_url"])
    if key and u.scheme != "https" and u.hostname not in LOOPBACK and not prof.get("allow_insecure"):
        raise ImgateError(
            f"Refusing to send an API key over plain http to {u.hostname}. Use https, drop the key, "
            "or set allow_insecure = true on this profile."
        )
    headers = {}
    if prof["style"] == "openrouter":
        if cfg.get("app_title"):
            headers["X-Title"] = cfg["app_title"]
        if cfg.get("app_url"):
            headers["HTTP-Referer"] = cfg["app_url"]
    return openai.OpenAI(api_key=key or "none", base_url=prof["base_url"], timeout=cfg["timeout"],
                         max_retries=cfg["max_retries"], default_headers=headers or None)


def wrap_api_errors(fn, prof: dict, timeout: float):
    import openai
    try:
        return fn()
    except openai.APITimeoutError:
        raise ImgateError(f"Timed out after {timeout}s. The server may still be generating (and billing).") from None
    except openai.APIConnectionError as e:
        raise ImgateError(redact(f"Cannot reach {prof['base_url']}: {e.__cause__ or e}")) from None
    except openai.APIStatusError as e:
        msg = e.message
        if isinstance(e.body, dict):
            err = e.body.get("error", e.body)
            msg = err.get("message", msg) if isinstance(err, dict) else str(err)
        elif e.status_code == 404 and "/images" in e.request.url.path:
            # A plain-text 404 (no JSON error) means the route itself is missing. Ollama answers this way
            # for /v1/images/generations and /v1/images/edits (checked on 0.40.17, 2026-11-22).
            msg = (f"{e.request.url.path} does not exist on this server. It does not implement the OpenAI "
                   "images API (Ollama, for one, has no /v1/images/* routes). Use an endpoint that does: "
                   "OpenRouter, vLLM-Omni, OpenAI.")
        raise ImgateError(redact(f"HTTP {e.status_code} from {prof['name']}: {str(msg)[:800]}")) from None


def url_allowed(url: str, base_url: str | None) -> bool:
    u = urlparse(url)
    if u.scheme == "https":
        return True
    return u.scheme == "http" and base_url is not None and u.hostname == urlparse(base_url).hostname


def download(url: str, timeout: float, base_url: str | None) -> bytes:
    if not url_allowed(url, base_url):
        raise ImgateError("Refusing to download an image from an untrusted non-https URL.")
    req = urllib.request.Request(url, headers={"User-Agent": f"imgate/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = r.read(MAX_DOWNLOAD_BYTES + 1)
    if len(buf) > MAX_DOWNLOAD_BYTES:
        raise ImgateError("Image download exceeded size limit.")
    return buf


# ---------------------------------------------------------------- request

def slugify(s: str, n: int = 40) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n].strip("-")) or "image"


def check_ref(cfg: dict, ref: str, restrict: bool) -> str:
    """Validate a reference (URL, data URL, or local path). Returns it unchanged."""
    if ref.startswith(("https://", "http://", "data:")):
        return ref
    p = Path(ref).expanduser().resolve()
    if restrict:
        allowed = [Path(d).expanduser().resolve() for d in [cfg["output_dir"], *cfg["mcp"]["reference_dirs"]]]
        if not any(p == d or d in p.parents for d in allowed):
            raise ImgateError(f"Reference path not allowed: {p}. Add its folder to [mcp].reference_dirs.")
    if not p.is_file():
        raise ImgateError(f"Reference file not found: {p}")
    if p.stat().st_size > MAX_REF_BYTES:
        raise ImgateError(f"Reference file too large (>20MB): {p}")
    if not (mimetypes.guess_type(p.name)[0] or "image/png").startswith("image/"):
        raise ImgateError(f"Reference is not an image: {p}")
    return str(p)


def ref_bytes(cfg: dict, ref: str) -> tuple[str, bytes, str]:
    """(filename, bytes, mime) for a validated reference."""
    if ref.startswith("data:"):
        head, _, b64 = ref.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return f"ref.{mime.split('/')[-1]}", base64.b64decode(b64), mime
    if ref.startswith(("http://", "https://")):
        if not ref.startswith("https://"):
            raise ImgateError("Reference URLs must be https.")
        data = download(ref, cfg["timeout"], None)
        mime = mimetypes.guess_type(urlparse(ref).path)[0] or "image/png"
        return Path(urlparse(ref).path).name or "ref.png", data, mime
    p = Path(ref)
    return p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png"


def ref_as_input(cfg: dict, ref: str) -> str:
    """URL or base64 data URL for OpenRouter's input_references."""
    if ref.startswith(("https://", "http://", "data:")):
        return ref
    _name, data, mime = ref_bytes(cfg, ref)
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def make_plan(cfg: dict, prompt: str, *, profile: str | None = None, model: str | None = None,
              preset: str | None = None, params: dict | None = None, refs: list[str] | None = None,
              provider: dict | None = None, extra: dict | None = None, restrict_refs: bool = False,
              mask: str | None = None) -> dict:
    if not prompt or not prompt.strip():
        raise ImgateError("Empty prompt.")
    pr = None
    if preset:
        pr = cfg["presets"].get(preset)
        if pr is None:
            raise ImgateError(f"Unknown preset '{preset}'. Available: {', '.join(cfg['presets']) or 'none'}")
    prof = get_profile(cfg, profile or (pr or {}).get("profile"))
    p: dict = {}
    deep_merge(p, cfg["defaults"])
    deep_merge(p, prof.get("defaults", {}))
    ex: dict = copy.deepcopy(prof.get("extra", {}))
    prov: dict = {}
    if prof["style"] == "openrouter":
        deep_merge(prov, cfg["provider"])
        deep_merge(prov, prof.get("provider", {}))
    chosen = prof.get("model")
    if pr:
        chosen = pr.get("model", chosen)
        prompt = f"{pr.get('prompt_prefix', '')}{prompt}{pr.get('prompt_suffix', '')}"
        p.update(pr.get("params", {}))
        deep_merge(ex, pr.get("extra", {}))
        if prof["style"] == "openrouter":
            deep_merge(prov, pr.get("provider", {}))
    chosen = model or chosen
    if not chosen:
        raise ImgateError(f"No model set for profile '{prof['name']}'. Pass -m or set `model` in the profile.")
    for k, v in (params or {}).items():
        if k not in PARAM_KEYS:
            raise ImgateError(f"Unknown parameter '{k}'. Use --set / extra for anything else.")
        if v is not None:
            p[k] = v
    if provider:
        if prof["style"] != "openrouter":
            raise ImgateError("`provider` routing only applies to style=openrouter profiles.")
        deep_merge(prov, provider)
    if extra:
        deep_merge(ex, extra)
    checked = [check_ref(cfg, r, restrict_refs) for r in refs or []]
    mask_ref = None
    if mask:
        if not checked:
            raise ImgateError("A mask needs at least one reference image (--ref).")
        mask_ref = check_ref(cfg, mask, restrict_refs)
        if not mask_ref.startswith(("http://", "https://")) and not checked[0].startswith(("http://", "https://")):
            m_sz, r_sz = png_size(ref_bytes(cfg, mask_ref)[1]), None
            if m_sz is None:
                raise ImgateError("Mask must be a PNG with transparency.")
            first = ref_bytes(cfg, checked[0])[1]
            r_sz = png_size(first)
            if r_sz and r_sz != m_sz:
                raise ImgateError(f"Mask size {m_sz} must equal the first reference image size {r_sz}.")
    return {"cfg": cfg, "prof": prof, "model": chosen, "prompt": prompt.strip(), "params": p,
            "extra": ex, "provider": prov, "refs": checked, "mask": mask_ref}


def _sig_params(fn) -> set[str]:
    try:
        return set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return set()


def ref_entry(prof: dict, url: str):
    """Encode one reference or markup entry for OpenRouter.

    OpenRouter requires {"type": "image_url", "image_url": {"url": ...}}. A plain string is rejected with
    HTTP 400 ("expected object, received string"; checked live 2026-11-22 for input_references and markup).
    `ref_shape = "string"` on a profile keeps the old behavior for an endpoint that wants it.
    """
    if prof.get("ref_shape", "image_url") == "string":
        return url
    return {"type": "image_url", "image_url": {"url": url}}


def plan_view(plan: dict) -> dict:
    """What will be sent, with reference contents redacted. Used for dry-run and sidecars."""
    prof = plan["prof"]
    refs = [f"<data-url {len(r)} chars>" if r.startswith("data:") else r for r in plan["refs"]]
    if prof["style"] == "openrouter":
        body = {"model": plan["model"], "prompt": plan["prompt"], **plan["params"]}
        if plan["provider"]:
            body["provider"] = plan["provider"]
        if refs:
            body["input_references"] = [ref_entry(prof, r) for r in refs]
        if plan["mask"]:
            body["markup"] = ref_entry(prof, "<data-url>" if plan["mask"].startswith("data:") else plan["mask"])
        deep_merge(body, plan["extra"])
        endpoint = "POST /images"
    else:
        body = {"model": plan["model"], "prompt": plan["prompt"], **plan["params"], **plan["extra"]}
        if refs:
            body["image"] = refs
        if plan["mask"]:
            body["mask"] = "<data-url>" if plan["mask"].startswith("data:") else plan["mask"]
        endpoint = "POST /images/edits" if refs else "POST /images/generations"
    return {"profile": prof["name"], "style": prof["style"], "base_url": prof["base_url"],
            "endpoint": endpoint, "body": body}


def call_api(plan: dict) -> dict:
    cfg, prof = plan["cfg"], plan["prof"]
    client = make_client(cfg, prof)
    if prof["style"] == "openrouter":
        body = {"model": plan["model"], "prompt": plan["prompt"], **plan["params"]}
        if plan["provider"]:
            body["provider"] = plan["provider"]
        if plan["refs"]:
            body["input_references"] = [ref_entry(prof, ref_as_input(cfg, r)) for r in plan["refs"]]
        if plan["mask"]:
            body["markup"] = ref_entry(prof, ref_as_input(cfg, plan["mask"]))
        deep_merge(body, plan["extra"])
        return wrap_api_errors(lambda: client.post("/images", cast_to=object, body=body), prof, cfg["timeout"])
    # style = openai
    fn = client.images.edit if plan["refs"] else client.images.generate
    known = _sig_params(fn)
    kwargs: dict = {"model": plan["model"], "prompt": plan["prompt"]}
    extra_body = dict(plan["extra"])
    for k, v in plan["params"].items():
        (kwargs if k in known else extra_body)[k] = v
    if extra_body:
        kwargs["extra_body"] = extra_body
    if plan["refs"]:
        files = [ref_bytes(cfg, r) for r in plan["refs"]]
        kwargs["image"] = files if len(files) > 1 else files[0]
        if plan["mask"]:
            kwargs["mask"] = ref_bytes(cfg, plan["mask"])
    resp = wrap_api_errors(lambda: fn(**kwargs), prof, cfg["timeout"])
    return resp.model_dump(exclude_none=True) if hasattr(resp, "model_dump") else dict(resp)


# ------------------------------------------------------------------- save

EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
       "image/svg+xml": "svg"}


def sniff_media_type(b: bytes) -> str | None:
    if b.startswith(b"\x89PNG"):
        return "image/png"
    if b.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:3] == b"GIF":
        return "image/gif"
    if b.lstrip()[:4] in (b"<svg", b"<?xm"):
        return "image/svg+xml"
    return None


def generate(cfg: dict, prompt: str, *, out_dir: str | None = None, subdir: str | None = None,
             name: str | None = None, dry_run: bool = False, lint: bool = True, **kw) -> dict:
    plan = make_plan(cfg, prompt, **kw)
    view = plan_view(plan)
    warnings = lint_prompt(plan["prompt"]) if lint and cfg.get("lint_prompt", True) else []
    if dry_run:
        return {"dry_run": True, "request": view, "warnings": warnings}
    resp = call_api(plan)
    items = resp.get("data") or []
    if not items:
        raise ImgateError(f"No images in response: {redact(json.dumps(resp, default=str)[:500])}")
    root = Path(out_dir or cfg["output_dir"]).expanduser()
    if subdir:
        root = root / slugify(subdir, 60)
    root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = (name and slugify(name, 60)) or f"{slugify(plan['prompt'], 40)}_{slugify(plan['model'].split('/')[-1], 30)}"
    files = []
    for i, it in enumerate(items):
        if it.get("b64_json"):
            raw = base64.b64decode(it["b64_json"])
        elif it.get("url"):
            raw = download(it["url"], cfg["timeout"], plan["prof"]["base_url"])
        else:
            raise ImgateError("Response item has neither b64_json nor url.")
        mt = it.get("media_type") or sniff_media_type(raw) or "image/png"
        ext = EXT.get(mt, plan["params"].get("output_format") or "png")
        path = root / f"{stamp}_{base}_{i + 1}.{ext}"
        c = 1
        while path.exists():
            c += 1
            path = root / f"{stamp}_{base}_{i + 1}-{c}.{ext}"
        path.write_bytes(raw)
        sidecar = path.with_suffix(path.suffix + ".json")
        sidecar.write_text(json.dumps({
            "tool": f"imgate {__version__}", "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
            "request": view, "revised_prompt": it.get("revised_prompt"),
            "response_meta": {k: resp.get(k) for k in ("created", "usage", "expires_at")},
            "file": path.name, "media_type": mt, "bytes": len(raw),
        }, indent=2, default=str))
        files.append({"path": str(path), "media_type": mt, "bytes": len(raw), "sidecar": str(sidecar)})
    return {"files": files, "profile": plan["prof"]["name"], "model": plan["model"],
            "seed": plan["params"].get("seed", plan["extra"].get("seed")),
            "usage": resp.get("usage"), "request": view, "warnings": warnings}


def list_models(cfg: dict, profile: str | None = None, filt: str | None = None) -> list[dict]:
    prof = get_profile(cfg, profile)
    client = make_client(cfg, prof)
    out = []
    if prof["style"] == "openrouter":
        resp = wrap_api_errors(lambda: client.get("/images/models", cast_to=object), prof, cfg["timeout"])
        for m in resp.get("data", []):
            out.append({"id": m.get("id"), "name": m.get("name"),
                        "supported_parameters": m.get("supported_parameters", {})})
    else:  # /models lists every served model, not only image models
        resp = wrap_api_errors(lambda: client.models.list(), prof, cfg["timeout"])
        out = [{"id": m.id, "name": m.id, "supported_parameters": {}} for m in resp.data]
    if filt:
        out = [m for m in out if filt.lower() in f"{m['id']} {m['name']}".lower()]
    return out


def profile_summary(cfg: dict) -> list[dict]:
    return [{"name": n, "style": p["style"], "base_url": p["base_url"], "model": p.get("model"),
             "api_key_env": p.get("api_key_env") or None, "default": n == cfg["default_profile"]}
            for n, p in cfg["profiles"].items()]


# -------------------------------------------------------------------- CLI

def parse_sets(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep or not k:
            raise ImgateError(f"--set expects key=value, got '{it}'")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cli_generate(a, cfg) -> int:
    if a.prompt_file:
        prompt = sys.stdin.read() if a.prompt_file == "-" else Path(a.prompt_file).expanduser().read_text()
    else:
        prompt = a.prompt or ""
    prov: dict = json.loads(a.provider_json) if a.provider_json else {}
    if a.only:
        prov["only"] = a.only
    if a.order:
        prov["order"] = a.order
    if a.no_fallbacks:
        prov["allow_fallbacks"] = False
    if a.zdr:
        prov["zdr"] = True
    if a.deny_data_collection:
        prov["data_collection"] = "deny"
    params = {k: getattr(a, k) for k in PARAM_KEYS if getattr(a, k, None) is not None}
    extra = json.loads(a.extra) if a.extra else {}
    deep_merge(extra, parse_sets(a.set))
    res = generate(cfg, prompt, out_dir=a.out_dir, subdir=a.subdir, name=a.name, dry_run=a.dry_run,
                   profile=a.profile, model=a.model, preset=a.preset, params=params, refs=a.ref,
                   provider=prov or None, extra=extra or None, mask=a.mask, lint=not a.no_lint)
    for w in res.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr)
    if a.json or a.dry_run:
        print(json.dumps(res, indent=2, default=str))
    else:
        for f in res["files"]:
            print(f["path"])
        cost = (res.get("usage") or {}).get("cost")
        if cost is not None:
            print(f"cost: ${cost}", file=sys.stderr)
    return 0


def run_batch(cfg: dict, rows: list[dict], concurrency: int = 1, dry_run: bool = False,
              default_subdir: str | None = None, max_jobs: int | None = None) -> list[dict]:
    limit = max_jobs or cfg["max_batch"]
    if len(rows) > limit:
        raise ImgateError(f"{len(rows)} jobs exceeds the limit of {limit}. Raise [max_batch] or pass --max-jobs.")
    from concurrent.futures import ThreadPoolExecutor

    def one(i_row):
        i, row = i_row
        try:
            row = dict(row)
            prompt = row.pop("prompt", None)
            pfile = row.pop("prompt_file", None)
            if prompt is None and pfile:
                prompt = Path(pfile).expanduser().read_text()
            refs = row.pop("ref", None) or row.pop("refs", None)
            refs = [refs] if isinstance(refs, str) else refs
            extra = row.pop("extra", None) or {}
            deep_merge(extra, row.pop("set", None) or {})
            params = {k: row.pop(k) for k in PARAM_KEYS if k in row}
            slug = row.pop("name", None) or slugify(prompt or "image", 40)
            kw = {k: row.pop(k, None) for k in ("profile", "model", "preset", "mask", "provider", "subdir")}
            if row:
                raise ImgateError(f"Unknown job field(s): {', '.join(row)}")
            res = generate(cfg, prompt or "", dry_run=dry_run, profile=kw["profile"], model=kw["model"],
                           preset=kw["preset"], params=params, refs=refs, mask=kw["mask"],
                           provider=kw["provider"], extra=extra or None,
                           subdir=kw["subdir"] or default_subdir, name=f"{slug}-j{i}")
            return {"job": i, "ok": True, **{k: res.get(k) for k in ("files", "profile", "model", "seed", "warnings", "request") if k in res},
                    "cost_usd": (res.get("usage") or {}).get("cost")}
        except ImgateError as e:
            return {"job": i, "ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"job": i, "ok": False, "error": redact(f"{type(e).__name__}: {e}")}

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, 4))) as ex:
        return list(ex.map(one, enumerate(rows, 1)))


def cli_batch(a, cfg) -> int:
    text = sys.stdin.read() if a.file == "-" else Path(a.file).expanduser().read_text()
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ImgateError(f"Line {n} is not valid JSON: {e}") from None
    if not rows:
        raise ImgateError("No jobs in file.")
    results = run_batch(cfg, rows, a.concurrency, a.dry_run, a.subdir, a.max_jobs)
    for r in results:
        print(json.dumps(r, default=str))
    bad = sum(not r["ok"] for r in results)
    cost = sum(r.get("cost_usd") or 0 for r in results)
    print(f"{len(results) - bad}/{len(results)} jobs ok" + (f", reported cost ${cost:.4f}" if cost else ""), file=sys.stderr)
    return 1 if bad else 0


def cli_check(a, cfg) -> int:
    prompt = (sys.stdin.read() if a.prompt_file == "-" else Path(a.prompt_file).expanduser().read_text()) \
        if a.prompt_file else (a.prompt or "")
    if not prompt.strip():
        raise ImgateError("Empty prompt.")
    res = check_prompt(prompt)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"{res['words']} words")
    for w in res["warnings"]:
        print(f"warning: {w}")
    for h in res["hints"]:
        print(f"hint: {h}")
    if not res["warnings"] and not res["hints"]:
        print("No issues found by the heuristic checks.")
    return 0


def cli_guide(a, cfg) -> int:
    if a.list:
        for title, _ in guide_sections(load_guide()):
            print(title)
        return 0
    print(get_guide(a.section))
    return 0


def cli_models(a, cfg) -> int:
    ms = list_models(cfg, a.profile, a.filter)
    if a.json:
        print(json.dumps(ms, indent=2))
        return 0
    for m in ms:
        print(f"{m['id']}  ({m['name']})" if m["name"] != m["id"] else m["id"])
        if a.verbose and m["supported_parameters"]:
            print("    params:", ", ".join(m["supported_parameters"]))
    return 0


def cli_init(a, _cfg) -> int:
    if a.print:
        print(EXAMPLE_CONFIG, end="")
        return 0
    p = Path("~/.config/imgate/config.toml").expanduser()
    if p.exists():
        print(f"{p} already exists; not overwriting.", file=sys.stderr)
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EXAMPLE_CONFIG)
    print(p)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="imgate", description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", aliases=["gen"], help="generate image(s)")
    g.add_argument("prompt", nargs="?")
    g.add_argument("--prompt-file", help="read prompt from file, or - for stdin")
    g.add_argument("-P", "--profile")
    g.add_argument("-m", "--model")
    g.add_argument("-p", "--preset")
    g.add_argument("-n", type=int)
    g.add_argument("--size")
    g.add_argument("--aspect-ratio", dest="aspect_ratio")
    g.add_argument("--resolution", help="512, 1K, 2K, 4K (OpenRouter)")
    g.add_argument("--quality")
    g.add_argument("--format", dest="output_format", choices=["png", "jpeg", "webp", "svg", "gif"])
    g.add_argument("--background")
    g.add_argument("--seed", type=int)
    g.add_argument("--response-format", dest="response_format", choices=["b64_json", "url"])
    g.add_argument("--ref", action="append", help="reference image: URL or local path (repeatable)")
    g.add_argument("--mask", help="PNG mask/overlay for edits (needs --ref). OpenAI-style: transparent "
                   "areas are edited. OpenRouter: sent as `markup`; semantics vary by model, test first.")
    g.add_argument("--no-lint", action="store_true", help="skip the prompt negation warning")
    g.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="extra request field, value parsed as JSON if possible (repeatable), "
                        "e.g. --set num_inference_steps=30")
    g.add_argument("--extra", help="raw JSON deep-merged into the request (last, wins)")
    g.add_argument("--only", action="append", help="[openrouter] provider slug to restrict to")
    g.add_argument("--order", action="append", help="[openrouter] provider preference order")
    g.add_argument("--no-fallbacks", action="store_true", help="[openrouter]")
    g.add_argument("--zdr", action="store_true", help="[openrouter] zero-data-retention endpoints only")
    g.add_argument("--deny-data-collection", action="store_true", help="[openrouter]")
    g.add_argument("--provider-json", help="[openrouter] raw JSON merged into `provider`")
    g.add_argument("--out-dir")
    g.add_argument("--subdir", help="subfolder under the output dir")
    g.add_argument("--name", help="base filename")
    g.add_argument("--dry-run", action="store_true", help="print the request, do not call the API")
    g.add_argument("--json", action="store_true")
    g.set_defaults(fn=cli_generate)

    b = sub.add_parser("batch", help="run many jobs from a JSONL file (one JSON object per line)")
    b.add_argument("file", help="JSONL path, or - for stdin")
    b.add_argument("--concurrency", type=int, default=1, help="parallel jobs, max 4")
    b.add_argument("--max-jobs", type=int)
    b.add_argument("--subdir")
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(fn=cli_batch)

    c = sub.add_parser("check", help="review a prompt: negation warnings and missing-information hints (no API call)")
    c.add_argument("prompt", nargs="?")
    c.add_argument("--prompt-file", help="read prompt from file, or - for stdin")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cli_check)

    gd = sub.add_parser("guide", help="print the prompting best-practices guide")
    gd.add_argument("--section", help="print only sections whose heading contains this text")
    gd.add_argument("--list", action="store_true", help="list section headings")
    gd.set_defaults(fn=cli_guide)

    m = sub.add_parser("models", help="list models for a profile")
    m.add_argument("-P", "--profile")
    m.add_argument("--filter")
    m.add_argument("-v", "--verbose", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cli_models)

    sub.add_parser("profiles", help="list profiles").set_defaults(
        fn=lambda a, c: print(json.dumps(profile_summary(c), indent=2)) or 0)
    sub.add_parser("presets", help="list presets").set_defaults(
        fn=lambda a, c: print(json.dumps(c["presets"], indent=2)) or 0)
    sub.add_parser("config", help="show effective config (no secrets are stored in it)").set_defaults(
        fn=lambda a, c: print(json.dumps(c, indent=2)) or 0)
    i = sub.add_parser("init", help="write an example config")
    i.add_argument("--print", action="store_true")
    i.set_defaults(fn=cli_init)
    sub.add_parser("serve", help="run MCP server on stdio").set_defaults(fn=lambda a, c: serve(c))

    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            return cli_init(a, None)
        return a.fn(a, load_config(a.config))
    except ImgateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


# -------------------------------------------------------------------- MCP

PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"]

_GEN_PROPS = {
    "prompt": {"type": "string", "description": "Image prompt. Phrase what you want positively; "
               "image models often ignore or invert negations."},
    "profile": {"type": "string", "description": "Endpoint profile (see list_profiles). Omit for the default."},
    "model": {"type": "string", "description": "Model id. Omit for the profile's default."},
    "preset": {"type": "string", "description": "Named preset from the config (see list_presets)."},
    "n": {"type": "integer", "minimum": 1},
    "size": {"type": "string", "description": "e.g. 1024x1024"},
    "aspect_ratio": {"type": "string", "description": "e.g. 16:9 (OpenRouter; extra field elsewhere)"},
    "resolution": {"type": "string", "description": "512, 1K, 2K or 4K (OpenRouter, model dependent)"},
    "quality": {"type": "string"},
    "output_format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
    "background": {"type": "string"},
    "seed": {"type": "integer", "description": "For reproducibility where the model supports it."},
    "references": {"type": "array", "items": {"type": "string"},
                   "description": "Reference images for image-to-image: https URLs or absolute local paths "
                   "inside an allowed folder."},
    "mask": {"type": "string", "description": "PNG mask/overlay for edits (requires references). Same "
             "dimensions as the first reference. Path rules as for references."},
    "subdir": {"type": "string", "description": "Subfolder name under the output dir."},
    "provider": {"type": "object", "description": "OpenRouter provider routing (only, order, "
                 "allow_fallbacks, zdr, data_collection, options). OpenRouter profiles only."},
    "extra": {"type": "object", "description": "Raw fields deep-merged into the request "
              "(e.g. num_inference_steps for vLLM-Omni)."},
}

MCP_INSTRUCTIONS = (
    "Image generation. Before generating from a vague, very short or negation-heavy prompt, or when the "
    "user asks for prompt help, call check_prompt (free) and prompt_guide for best practices. Rewrite "
    "negations as positive statements, keep the user's wording and intent, mark anything you add as a "
    "suggestion, and tell the user what you changed. Ask at most three questions (purpose and aspect "
    "ratio, style, exact text). Confirm before costly settings (n above 1, 4K). After generating, report "
    "the file path, final prompt, profile, model, seed and cost. Never replace a failed raster request "
    "with an SVG or HTML drawing.")

TOOLS = [
    {"name": "generate_image",
     "description": "Generate image(s) via OpenRouter or a self-hosted OpenAI-compatible server. Saves "
                    "files plus a JSON sidecar (full request, seed, cost) and returns paths and a preview.",
     "inputSchema": {"type": "object", "properties": _GEN_PROPS, "required": ["prompt"]}},
    {"name": "list_image_models",
     "description": "List models for a profile. For OpenRouter this is image models with supported "
                    "parameters; for OpenAI-style servers it is everything the server lists.",
     "inputSchema": {"type": "object", "properties": {"profile": {"type": "string"}, "filter": {"type": "string"}}}},
    {"name": "check_prompt",
     "description": "Review an image prompt before generating (no API call, no cost): negation warnings "
                    "and hints about missing style, framing, lighting or unquoted text. Heuristic only.",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}},
    {"name": "prompt_guide",
     "description": "Return the image-prompting best-practices guide (spec fields, negation handling, "
                    "text, edits, vendor notes for FLUX.2, Gemini, GPT Image). Use it to coach the user. "
                    "Optionally pass a section keyword such as 'negations' or 'edits'.",
     "inputSchema": {"type": "object", "properties": {"section": {"type": "string"}}}},
    {"name": "list_profiles", "description": "List configured endpoint profiles (no secrets).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_presets", "description": "List configured presets.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _text(obj) -> dict:
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=2, default=str)}


def call_tool(cfg: dict, name: str, args: dict) -> dict:
    mcp = cfg["mcp"]
    try:
        if name == "list_presets":
            return {"content": [_text(cfg["presets"])]}
        if name == "list_profiles":
            return {"content": [_text(profile_summary(cfg))]}
        if name == "check_prompt":
            if not (args.get("prompt") or "").strip():
                raise ImgateError("Empty prompt.")
            return {"content": [_text(check_prompt(args["prompt"]))]}
        if name == "prompt_guide":
            return {"content": [_text(get_guide(args.get("section")))]}
        allowed_p = mcp.get("allowed_profiles") or []
        pname = args.get("profile")
        if name == "list_image_models":
            eff = get_profile(cfg, pname)["name"]
            if allowed_p and eff not in allowed_p:
                raise ImgateError(f"Profile '{eff}' is not in [mcp].allowed_profiles: {allowed_p}")
            return {"content": [_text(list_models(cfg, pname, args.get("filter")))]}
        if name != "generate_image":
            return {"content": [_text(f"Unknown tool {name}")], "isError": True}
        pr = cfg["presets"].get(args.get("preset") or "", {}) or {}
        prof = get_profile(cfg, pname or pr.get("profile"))
        if allowed_p and prof["name"] not in allowed_p:
            raise ImgateError(f"Profile '{prof['name']}' is not in [mcp].allowed_profiles: {allowed_p}")
        allowed_m = mcp.get("allowed_models") or []
        effective = args.get("model") or pr.get("model") or prof.get("model")
        if allowed_m and effective not in allowed_m:
            raise ImgateError(f"Model '{effective}' is not in [mcp].allowed_models: {allowed_m}")
        n = args.get("n")
        if n and n > mcp.get("max_n", 4):
            raise ImgateError(f"n={n} exceeds [mcp].max_n={mcp.get('max_n', 4)}")
        params = {k: args[k] for k in PARAM_KEYS if k in args and k != "response_format"}
        res = generate(cfg, args.get("prompt", ""), subdir=args.get("subdir"), profile=pname,
                       model=args.get("model"), preset=args.get("preset"), params=params,
                       refs=args.get("references"), provider=args.get("provider"),
                       extra=args.get("extra"), mask=args.get("mask"), restrict_refs=True)
        summary = {"files": [f["path"] for f in res["files"]], "profile": res["profile"],
                   "model": res["model"], "seed": res["seed"], "cost_usd": (res.get("usage") or {}).get("cost")}
        if res.get("warnings"):
            summary["warnings"] = res["warnings"]
        content = [_text(summary)]
        if mcp.get("inline_images", True):
            for f in res["files"]:
                if f["bytes"] <= mcp.get("max_inline_bytes", 1_000_000) and f["media_type"] != "image/svg+xml":
                    data = base64.b64encode(Path(f["path"]).read_bytes()).decode()
                    content.append({"type": "image", "data": data, "mimeType": f["media_type"]})
                else:
                    content.append(_text(f"(not inlined, too large or unsupported: {f['path']})"))
        return {"content": content}
    except ImgateError as e:
        return {"content": [_text(f"error: {e}")], "isError": True}
    except Exception as e:  # noqa: BLE001
        return {"content": [_text(redact(f"internal error: {type(e).__name__}: {e}"))], "isError": True}


def serve(cfg: dict) -> int:
    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, p = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method is None or mid is None:  # response to us, or a notification
            continue
        if method == "initialize":
            want = p.get("protocolVersion")
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": want if want in PROTOCOLS else PROTOCOLS[-1],
                "capabilities": {"tools": {}}, "serverInfo": {"name": "imgate", "version": __version__},
                "instructions": MCP_INSTRUCTIONS}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": mid,
                  "result": call_tool(cfg, p.get("name", ""), p.get("arguments") or {})})
        elif method in ("resources/list", "prompts/list", "resources/templates/list"):
            key = {"resources/list": "resources", "prompts/list": "prompts",
                   "resources/templates/list": "resourceTemplates"}[method]
            send({"jsonrpc": "2.0", "id": mid, "result": {key: []}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
