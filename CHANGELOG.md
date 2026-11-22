# Changelog

All notable changes to imgate. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (pre-1.0: a minor bump may change behavior).

## 0.5.0 - 2026-11-22

First release checked against a live OpenRouter endpoint and a live Ollama server. The details are in the
README under "Verification status".

### Fixed

- OpenRouter reference images and masks now work. `input_references` and `markup` entries are sent as
  `{"type":"image_url","image_url":{"url":...}}`. Live OpenRouter rejects a plain string with HTTP 400
  (`expected object, received string`), so every reference edit failed in 0.4.0.
- A plain-text `404 page not found` on an images route now says the server does not implement the OpenAI
  images API, instead of `HTTP 404: Error code: 404`.

### Added

- `mask_field` profile setting for `openai`-style servers that name the edit mask differently. vLLM-Omni reads
  `mask_image`, so the SDK's `mask` part was ignored there. Taken from its source, untested against a live
  server. Default stays `mask`.
- GitHub Actions CI: `uv sync --locked`, the offline tests and `ruff check` on Python 3.12 and 3.13.
- `ref_shape` is validated (`image_url` or `string`).

### Changed

- **Behavior change:** the default `ref_shape` is now `image_url`. Set `ref_shape = "string"` on a profile to
  send bare URLs again.
- The dry-run view and sidecars show reference and markup entries in the same shape that is sent.
- The built-in example config no longer enables an `ollama` profile. Ollama has no `/v1/images/*` routes
  (404 on 0.40.17), and its old default model name `z-image` is not an Ollama registry name. A config written
  by 0.4.0 `init` still has that profile; delete it.
- `ruff check` passes with ruff's default rules; the script is marked executable.
- README: dated lists of what was verified live, what comes from primary sources, and what is unverified.
  The MCP registration command no longer uses `-e OPENROUTER_API_KEY=$OPENROUTER_API_KEY`, which wrote the key
  in plain text to `~/.claude.json`.
- The test mock now mirrors live OpenRouter validation, and 10 checks were added (85 total).

### Observed, not a documented convention

- On `openai/gpt-image-2.6-sunburst`, OpenRouter's `markup` overlay marked the edit area with its opaque
  pixels in 2 of 2 trials, the opposite of OpenAI's `mask`. The model re-renders the whole image. No
  OpenRouter endpoint lists `markup` in `supported_parameters`.

## 0.4.0 - 2026-11-22

Initial release: CLI, MCP server (stdio) and Claude skill for image generation through OpenRouter and
self-hosted OpenAI-compatible endpoints. Profiles, two API styles (`openrouter`, `openai`), presets, batch,
masks, prompt `check` and `guide`, MCP guards, JSON sidecars. Tested only against local mock servers.
