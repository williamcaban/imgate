---
name: imgate
description: Generate or edit images through OpenRouter or a self-hosted OpenAI-compatible server (vLLM-Omni, stable-diffusion.cpp, Draw Things, OpenAI) using the bundled imgate.py CLI. Use when the user asks to generate, render, draw, or create an image, or to turn an image prompt into an actual picture.
---

# Image generation (OpenRouter and self-hosted)

Run the bundled script with the Python 3.12+ environment that has the `openai` package installed, for example `~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py ...`, or `uv run ~/.claude/skills/imgate/imgate.py ...` (the script carries its own dependency metadata). Define `IMGATE` once per shell and use it below.

## Rules

- API keys come only from environment variables named by the profile (`api_key_env`). Never ask the user to paste a key into chat, never print it, never write it into files. Self-hosted profiles usually need no key.
- Use the default profile. A profile that bills money (`openrouter`) is only for a request that names it, and needs the user's explicit OK in that turn even for a single image. Never switch to it because the default failed.
- Confirm with the user before spending money: `-n` above 1, `--resolution 4K`, or high quality on an expensive paid model. Self-hosted profiles cost GPU time, not money, but large `n` still takes a while.
- Before writing or revising a prompt, read `references/prompting.md` (spec fields, use-case categories, edit discipline, negation handling).
- Rewrite negations as positive statements ("one open Bible, alone in the manger", not "no other books"). The tool prints a `warning:` when a prompt contains negation words (quoted text is ignored; `--no-lint` silences it). If you see the warning, or the user's prompt has negations, rewrite positively, tell the user what you changed, and use `--set negative_prompt=...` only on servers known to accept it.
- If generation fails, report the failure. Never substitute an SVG or HTML drawing for a raster request unless vector output was requested.
- When done, report: saved path, final prompt (including any rewrite), profile, model, seed, and cost if reported.
- Parameter support differs per model and server. For OpenRouter run `models -v` and check `supported_parameters` first. For self-hosted servers, unknown fields may be ignored or rejected. If the server rejects one, drop it instead of guessing.

## Prompt coaching (help the user write a good prompt)

Coach when the user asks for prompt help or best practices, when their prompt is vague or very short, when `check` or the generate lint reports something, or after two unsatisfying results in a row. Skip coaching when they say to just run it, or when the prompt is already detailed.

1. Run `$IMGATE check "PROMPT"` (no cost). Read `references/prompting.md` for the guidance, and `$IMGATE guide --section negations` (or `edits`, `text`) if you need one part. The `check` hints are keyword heuristics, so use judgment.
2. Ask at most three short questions, and only about missing high-impact information: what the image is for (and aspect ratio), the style or medium, and any exact text to render. Do not interrogate. If the user gave enough, skip the questions.
3. Show one improved prompt that keeps the user's own wording and intent. Mark anything you added as a suggestion. Do not invent characters, brands, slogans or palettes.
4. Follow it with two or three bullets on what changed and why, each tied to a principle (positive phrasing, quoted text, framing, light). Teach one habit at a time. Do not lecture.
5. Adapt to the model in use. Check the model-specific notes in the guide (for example FLUX.2 has no negative prompts, Gemini prefers narrative description).
6. Suggest a cheap draft first (a fast model, or a local profile), then the final render. Show `--dry-run` for anything costly.
7. After a result, check it against the request and propose one change at a time, reusing the same `--seed`.

## Profiles

A profile picks the endpoint and API style. `profiles` lists them, `-P NAME` selects one.

| style | endpoints | use for |
|---|---|---|
| `openrouter` | `POST /images`, `GET /images/models` | OpenRouter (native API) |
| `sdapi` | `POST /sdapi/v1/txt2img`, `/img2img` | Draw Things app (HTTP API server). Only `-n`, `--size`, `--seed`; the rest via `--set` using the app's key names. No masks. Overwrites the app's prompt field |
| `openai` | `POST /images/generations`, `/images/edits`, `GET /models` | OpenAI, vLLM-Omni, stable-diffusion.cpp `sd-server` (set `extra_in_prompt = true` so `--seed` works), other OpenAI-compatible servers |

## Commands

```bash
IMGATE="python3 ~/.claude/skills/imgate/imgate.py"   # use the venv's python if needed

$IMGATE check "PROMPT"                                   # review a prompt, no API call, no cost
$IMGATE guide --list                                      # prompting best practices (--section negations)
$IMGATE profiles
$IMGATE models -P openrouter --filter flux -v
$IMGATE generate "PROMPT" --dry-run                       # show the request, no API call
$IMGATE generate "PROMPT" -p cinematic                    # preset
$IMGATE generate --prompt-file prompt.md -m black-forest-labs/flux.2-pro --aspect-ratio 16:9 --seed 42 --subdir nativity
$IMGATE generate "PROMPT" -P vllm-omni --size 1024x1024 --seed 42 --set num_inference_steps=30
$IMGATE generate "Change only the sky to dusk; keep everything else identical" --ref ./input.png   # image-to-image
$IMGATE generate "Replace the sign with ..." --ref ./input.png --mask ./mask.png   # masked edit, PNG, same size as the ref
$IMGATE batch jobs.jsonl --concurrency 2 --dry-run        # many jobs, one JSON object per line
$IMGATE generate "PROMPT" --zdr --deny-data-collection    # OpenRouter privacy routing
$IMGATE generate "PROMPT" --extra '{"any": {"raw": "json"}}'
```

Batch files: one JSON object per line with `prompt` (or `prompt_file`), and optionally `profile`, `model`, `preset`, `ref`, `mask`, `subdir`, `name`, `set`, `extra`, `provider`, plus any flat parameter (`n`, `size`, `seed`, `aspect_ratio`, ...). Lines starting with `#` are ignored. Runs at most `max_batch` jobs (default 20) and at most 4 in parallel. Always run `--dry-run` first and confirm the job count and models with the user, since every job can cost money. Each result prints as a JSON line; failed jobs do not stop the others.

Long prompts: save to a file and pass `--prompt-file` (or `-` for stdin) instead of shell-quoting.

For `openai`-style profiles, `--size`, `-n`, `--quality`, `--format`, `--background` map to the standard request fields. `--seed`, `--aspect-ratio`, `--resolution` and any `--set key=value` are sent as extra body fields, which servers like vLLM-Omni accept (for example `seed`, `num_inference_steps`).

## Edits and masks

Through MCP, a reference must be a file inside the output folder or `[mcp].reference_dirs`, or an https URL. An image the user attached in the chat is not on disk, so ask for a path, or have them copy the file into the output folder. On the Draw Things app, leave `strength` unset when the app's model is an instruction-edit model (Qwen-Image, FLUX.2 klein): the app default of 1 followed the instruction, while `--set strength=0.3` and `0.8` kept the source almost unchanged. Lower `strength` only for a classic img2img model, where it is the amount of change.

State what must stay unchanged, make one change per pass, and label each input image's role. `--mask` needs a PNG the same pixel size as the first `--ref`. On OpenAI-style servers transparent mask areas are edited (OpenAI's documented convention). vLLM-Omni names the field `mask_image`, so set `mask_field = "mask_image"` on that profile (untested live). On OpenRouter the mask is sent as `markup`: only some models honor it (no endpoint lists it in `supported_parameters`), and on `openai/gpt-image-2.6-sunburst` opaque pixels marked the edit area in 2 of 2 trials. That is guidance, not an exact inpaint, so test with a trivial edit first.

## Output

Each image is saved as `<output_dir>/[subdir/]<timestamp>_<slug>_<n>.<ext>` with a `.json` sidecar holding the exact request (reference images omitted), seed, usage and cost when reported. The command prints saved paths. To iterate reproducibly, reuse the same `--seed` and change one thing at a time, then compare sidecars.

## Config

`$IMGATE init` writes `~/.config/imgate/config.toml` (profiles, defaults, provider routing, presets, MCP guards). Lookup order: `$IMGATE_CONFIG`, `./imgate.toml`, `~/.config/imgate/config.toml`. `$IMGATE config` prints the effective config.

## Errors

- `HTTP 401/403`: key missing, wrong, or no credits.
- `HTTP 400/422` naming a parameter: the model or server does not support it.
- `Cannot reach ...`: the self-hosted server is down or the `base_url` is wrong (include the `/v1` suffix).
- `Refusing to send an API key over plain http`: use https, remove the key, or set `allow_insecure = true` on that profile.
- `openai package is not installed`: install it in the Python that runs the script.
