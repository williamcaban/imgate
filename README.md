# imgate

One gateway to many image-generation endpoints, for Claude Code and Claude Desktop.

imgate is a single Python file (3.12+, one dependency: `openai`) that works as a CLI and as an MCP
server. You configure named **profiles**, each pointing at an endpoint: OpenRouter, a self-hosted
vLLM-Omni or Ollama server, OpenAI, or any other OpenAI-compatible server. You choose a profile
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
               OpenRouter         vLLM-Omni         Ollama / OpenAI / other
              (hosted, many       (self-hosted,     (OpenAI-compatible)
               models, billed)     one model per
                                   server)
```

**Profiles pick the endpoint.** A profile in `config.toml` holds a `base_url`, an API `style`, a
default `model`, and the name of the environment variable that holds its key. `imgate profiles`
lists them, `-P name` selects one, and `default_profile` or `IMGATE_PROFILE` sets the default.

**Two API styles cover the field.**

- `openrouter`: OpenRouter's native `POST /images` and `GET /images/models`. OpenRouter's docs
  describe only `/images`, not the OpenAI SDK path, so imgate sends this through the openai
  client's generic `post` and `get`. You still get its auth, timeouts and retries. Provider routing
  (`only`, `order`, `zdr`, `data_collection`, `allow_fallbacks`) applies to this style only.
- `openai`: `POST /images/generations`, `POST /images/edits` and `GET /models` through the openai
  client's `images` methods. Use it for vLLM-Omni, Ollama (experimental), OpenAI and other
  OpenAI-compatible servers.

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

| | OpenRouter | vLLM-Omni | Ollama | OpenAI |
|---|---|---|---|---|
| imgate style | `openrouter` | `openai` | `openai` | `openai` |
| Models | many, per request | one per server | whatever you pulled | per request |
| Cost reporting | `usage.cost` when returned | none | none | varies |
| Image edits | `input_references`, optional `markup` mask | `/images/edits` (model dependent) | unverified | `/images/edits` with mask |
| Maturity | documented API | documented API | experimental | documented API |

Support differs per model and server. Check the model's docs, or `imgate models -v` on OpenRouter,
before relying on a parameter. Unverified in this release: behavior against live OpenRouter, vLLM-Omni
and Ollama endpoints (tests use a local mock), OpenRouter's mask convention, and whether OpenRouter
expects reference entries as strings or objects (`ref_shape` switches it).

## Install (macOS example)

    python3.12 -m venv ~/.venvs/imgate && ~/.venvs/imgate/bin/pip install openai
    mkdir -p ~/.claude/skills && cp -R skills/imgate ~/.claude/skills/
    ~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py init   # writes ~/.config/imgate/config.toml
    export OPENROUTER_API_KEY=...        # in your shell profile; never in chat or config

With uv instead: `uv run ~/.claude/skills/imgate/imgate.py ...` reads the dependency block in the script header.

Edit `~/.config/imgate/config.toml` to point `[profiles.vllm-omni]` and `[profiles.ollama]` at your servers.

## Prompt help

`imgate.py check "prompt"` reviews a prompt for free (negations, missing style, framing, light, unquoted text).
`imgate.py guide [--section negations]` prints the best-practices guide. The MCP server exposes the same as
`check_prompt` and `prompt_guide`, and tells the client to use them when coaching.

## Claude Code MCP (optional, in addition to the skill)

    claude mcp add imgate -s user -e OPENROUTER_API_KEY=$OPENROUTER_API_KEY -- ~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py serve

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
read reference images only from `output_dir` and `reference_dirs`.

## Credits

Prompting guidance in `references/prompting.md` is adapted from the MIT-licensed `imagegen` skill in openai/skills
(scaffold and use-case categories), rewritten here, plus vendor guidance linked in that file.

## Tests

    python3 tests/test_imgate.py     # offline: real openai client against a localhost mock

## Development

    uv sync                                  # Python 3.12 + openai + ruff
    uv run python tests/test_imgate.py       # offline tests
    uv run ruff check skills tests

## License

Apache-2.0. See `LICENSE`.
