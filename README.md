# imgate

One gateway to many image-generation endpoints, for Claude Code and Claude Desktop.

imgate is a single Python file (3.12+, one dependency: `openai`) that works as a CLI and as an MCP
server. You configure named **profiles**, each pointing at an endpoint: OpenRouter, a self-hosted
vLLM-Omni server, OpenAI, or any other OpenAI-compatible server that has image routes. You choose a profile
per request, and the same commands, presets, guards and output format work against all of them.

## Multi-endpoint design

```
 Claude Code (skill + CLI)   Claude Desktop (MCP)   your shell / scripts
              \                     |                     /
               +--------------  imgate.py  --------------+
                   check, generate, batch, models, guide, serve
                                    |
                      profile  ->  resolve endpoint
                      preset   ->  model, prompt wrapper, params
                      request  ->  explicit flags win
                                    |
              +---------------------+----------------------+
              | style = "openrouter"        style = "openai" |
              | POST /images                POST /images/generations
              | GET  /images/models         POST /images/edits
              | (OpenRouter native)         GET  /models
              +---------------------+----------------------+
                     |                |               |
               OpenRouter         vLLM-Omni         OpenAI / other
              (hosted, many       (self-hosted,     (OpenAI-compatible)
               models, billed)     one model per
                                   server)
```

**Profiles pick the endpoint.** A profile in `config.toml` holds a `base_url`, an API `style`, a
default `model`, and the name of the environment variable that holds its key. `imgate profiles`
lists them, `-P name` selects one, and `default_profile` or `IMGATE_PROFILE` sets the default.

**Three API styles cover the field.**

- `openrouter`: OpenRouter's native `POST /images` and `GET /images/models`. OpenRouter's docs
  describe only `/images`, not the OpenAI SDK path, so imgate sends this through the openai
  client's generic `post` and `get`. You still get its auth, timeouts and retries. Provider routing
  (`only`, `order`, `zdr`, `data_collection`, `allow_fallbacks`) applies to this style only.
- `openai`: `POST /images/generations`, `POST /images/edits` and `GET /models` through the openai
  client's `images` methods. Use it for vLLM-Omni, OpenAI and other
  OpenAI-compatible servers.
- `sdapi`: the Draw Things app's HTTP API server, which speaks the AUTOMATIC1111 API: `POST /sdapi/v1/txt2img`
  and `/img2img`. It rejects unknown fields, so this style is strict: only `-n`, `--size` and `--seed` map to
  it, other flags are an error, and the app's own fields go through `--set`.

**One request model, routed per style.** You set parameters once (`-n`, `--size`, `--seed`,
`--aspect-ratio`, `--set key=value`, ...). For `openai` style, imgate reads the installed SDK's
method signature: fields the SDK declares go as normal arguments, and everything else (`seed`,
`aspect_ratio`, `num_inference_steps`, ...) is sent in the request body as an extra field. So a new
server-specific field needs no code change.

**Presets sit above profiles.** A preset bundles a model, a prompt prefix or suffix, parameters, and
optionally a profile. For example, a `local-draft` preset can pin a self-hosted profile for cheap
drafts while `cinematic` targets OpenRouter for the final render.

**Safety is per profile and per MCP.**

- Keys come from environment variables named by the profile, never from the config file.
- A key is only sent over https, or over http to localhost, unless the profile sets
  `allow_insecure = true`.
- Self-hosted profiles usually need no key.
- The MCP server can restrict `allowed_profiles` and `allowed_models`, cap `n`, and read reference
  images only from approved folders. It cannot choose an output directory.

**Same output everywhere.** Each image is saved with a JSON sidecar holding the exact request
(without image contents or keys), seed, usage and cost when the endpoint reports it. Results from
different endpoints are therefore comparable.

### What differs by endpoint

| | OpenRouter | vLLM-Omni | OpenAI |
|---|---|---|---|
| imgate style | `openrouter` | `openai` | `openai` |
| Models | many, per request | one per server | per request |
| Cost reporting | `usage.cost` (seen live) | none | varies |
| Image edits | `input_references` objects, optional `markup` overlay | `/images/edits` on edit-capable models; mask field is `mask_image` (`mask_field`) | `/images/edits`, transparent mask areas are edited |
| Status | checked live 2026-11-22 | read from docs and source, not live-tested | not tested |

Support differs per model and server. Check the model's docs, or `imgate models -v` on OpenRouter, before
relying on a parameter. `markup` is the exception: no OpenRouter endpoint lists it in `supported_parameters`.

### Verification status (2026-11-22)

Checked live (OpenRouter, model `black-forest-labs/flux-3-image-klein` unless noted; about $0.09 spent):

- `GET /images/models` works without a key and lists per-model `supported_parameters`. Per-endpoint prices
  are at `/images/models/{id}/endpoints`.
- `POST /images` returns `data[].b64_json` by default, or `data[].url` plus `expires_at` with
  `response_format=url`, and `usage.cost` (0.008, equal to the published price). `created` can be 0.
- `input_references` and `markup` entries must be objects (`{"type":"image_url","image_url":{"url":...}}`).
  A plain string gets HTTP 400 `expected object, received string`. imgate 0.4.0 sent strings by default, so
  every reference edit failed. Fixed: objects are the default, `ref_shape = "string"` is an opt-in.
- A reference edit with the object shape works (sky changed, subject kept).
- `markup` on `openai/gpt-image-2.6-sunburst`: opaque pixels marked where the edit applied in 2 of 2 trials
  (a mask with the left half opaque put the object on the left, the mirrored mask put it on the right). That
  is the opposite of OpenAI's `mask`. The model re-renders the whole picture, so it is guidance, not a
  pixel-exact inpaint. Two trials on one model is not a convention.

Checked live against the Draw Things app (HTTP API server on `127.0.0.1:7860`, FLUX.2 klein 4B q8 selected in
the app, `sdapi` style):

- `txt2img`, `img2img` (a reference edit), `-n 2` (two images) and `--set steps=6` work. Image-to-image was
  rechecked with Qwen-Image 2.1 turbo selected in the app, through the MCP server as well as the CLI: the
  edit followed the prompt and kept the subject. `--set strength=0.3` and `0.8` are sent and change the
  result, but on that model they kept the source nearly unchanged, so leave `strength` unset for
  instruction-edit models. About 19 s for a
  768x768 text-to-image, 37 s for an edit, 55 s for two images at 6 steps.
- Requests start from the app's current settings and override only the fields sent. Of 87 settings, only
  `prompt` changed in the app afterwards: **an API call overwrites the app's prompt field.** Seed, size and
  steps did not persist.
- With no seed sent, the app uses its own fixed seed, so repeated runs gave the same picture. imgate now picks a
  random seed and records it. The same seed gives identical pixels (file bytes differ: the app embeds
  `eXIf` and `iTXt` metadata).
- Unknown fields are an HTTP 422 `Unrecognized keys: [...]`. The `init_images` size must equal `width` and
  `height`, so imgate takes the size from the reference and rejects a different `--size`.
- Not tested: masks (the source has no mask field, so `--mask` is rejected), `loras` and `controls` through
  `--set`, `strength` on models that are not editing models, and the gRPC option, which is Draw Things' own
  protocol and is not supported.

Taken from primary sources, not tested live:

- OpenAI `/images/edits` `mask`: fully transparent (alpha zero) areas are edited, the mask applies to the first
  image, PNG under 4 MB, same dimensions as the image (text from the official `openai` SDK 3.57.0).
- vLLM-Omni (`docs/serving/image_edit_api.md`, `api_server.py`, `docs/models/supported_models.md`):
  `/v1/images/edits` exists and takes `image`/`image[]` or `url`/`url[]`; `model` must match the served model or
  it is a 400; `response_format` must be `b64_json`; the mask field is `mask_image`, there is no `mask`;
  edit-capable models include Qwen-Image-Edit, -2509 and -2511, LongCat-Image-Edit, Boogu-Image Edit, Flux3
  and HunyuanImage-3 Instruct. A plain T2I model such as `Qwen/Qwen-Image` cannot edit.

Unverified:

- vLLM-Omni against a live server. In particular whether `mask_image` is accepted as a form string (the
  source declares it without `Form()`), and its mask polarity: the docs say white areas are inpainted, a
  code comment says the alpha channel matters. `mask_field = "mask_image"` is untested.
- `markup` polarity on any other model, and which models honor it.
- The OpenAI endpoint.

## Install (macOS example)

    python3.12 -m venv ~/.venvs/imgate && ~/.venvs/imgate/bin/pip install openai
    mkdir -p ~/.claude/skills && cp -R skills/imgate ~/.claude/skills/
    ~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py init   # writes ~/.config/imgate/config.toml
    export OPENROUTER_API_KEY=...        # in your shell profile; never in chat or config

With uv instead: `uv run ~/.claude/skills/imgate/imgate.py ...` reads the dependency block in the script header.

Edit `~/.config/imgate/config.toml` to point `[profiles.vllm-omni]` at your server.

To keep the repo as the source of truth, symlink instead of copying:
`ln -sfn "$PWD/skills/imgate" ~/.claude/skills/imgate`. Claude Code follows the link.

## Draw Things (macOS app)

In the app, enable the API server and choose **HTTP** (not gRPC, which is the app's own protocol). Your app then
listens on `127.0.0.1` at the port it shows (7860 on the machine tested). Add:

    [profiles.drawthings]
    style    = "sdapi"
    base_url = "http://127.0.0.1:7860"      # no /v1
    model    = "app"                        # "app" = whatever model is selected in the app

    imgate.py generate "..." -P drawthings --size 768x768 --seed 7 --set steps=4
    imgate.py generate "Change only the sky to dusk" -P drawthings --ref ./input.png --seed 7

The model, sampler, steps and guidance come from the app's current settings unless you pass `--set steps=...`,
`--set guidance_scale=...`, `--set negative_prompt=...` (the app's own key names, with `cfg_scale` and
`n_iter`-style aliases). `imgate.py models -P drawthings` shows the selected model. Each call overwrites the app's
prompt field, so do not run it while you are writing a prompt there.

## Prompt help

`imgate.py check "prompt"` reviews a prompt for free (negations, missing style, framing, light, unquoted text).
`imgate.py guide [--section negations]` prints the best-practices guide. The MCP server exposes the same as
`check_prompt` and `prompt_guide`, and tells the client to use them when coaching.

## Claude Code MCP (optional, in addition to the skill)

    claude mcp add imgate -s user -- ~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py serve

Do not add `-e OPENROUTER_API_KEY=$OPENROUTER_API_KEY`: the shell expands it and the key is written in plain
text to `~/.claude.json`. Export the key in the environment that launches Claude Code. That the MCP server
inherits it is unverified; if `list_image_models` reports a missing key, set it with `claude mcp add -e`
yourself.

## Claude Desktop (Settings > Developer > Edit Config, then restart)

    {
      "mcpServers": {
        "imgate": {
          "command": "/Users/YOU/.venvs/imgate/bin/python",
          "args": ["/Users/YOU/.claude/skills/imgate/imgate.py", "serve"],
          "env": { "OPENROUTER_API_KEY": "sk-or-..." }
        }
      }
    }

Use absolute paths: Desktop does not inherit your shell PATH. The Python must be 3.12+ with `openai` installed.
Add `"IMGATE_CONFIG": "/Users/YOU/.config/imgate/config.toml"` to `env` if you keep the config elsewhere.

## MCP guards (`[mcp]` in config)

`allowed_profiles`, `allowed_models`, `max_n` limit cost and reach. The tool cannot pick an output directory, and can
read reference images only from `output_dir` and `reference_dirs`. An image attached in the chat is not a file the
tool can read; see `docs/claude-desktop.md`, "Image to image".

## Credits

Prompting guidance in `references/prompting.md` is adapted from the MIT-licensed `imagegen` skill in openai/skills
(scaffold and use-case categories), rewritten here, plus vendor guidance linked in that file.

## Tests

    python3 tests/test_imgate.py     # offline: real openai client against a localhost mock

## Development

    uv sync                                  # Python 3.12 + openai + ruff
    uv run python tests/test_imgate.py       # offline tests
    uv run ruff check skills tests

## More documentation

Setup notes for specific servers are in `docs/`.

## License

Apache-2.0. See `LICENSE`.
