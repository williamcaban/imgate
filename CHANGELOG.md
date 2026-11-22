# Changelog

All notable changes to imgate. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/) (pre-1.0: a minor bump may change behavior).

## Unreleased

### Added

- `IMGATE_MCP_ALLOWED_PROFILES` (comma list) narrows the profiles an MCP server may use, per connector, and
  `list_profiles` shows only those. An unknown name is a startup error. Used to run one connector for the free
  local profile and another for OpenRouter, so the host's approval prompt can guard the paid one. See
  `docs/claude-desktop.md`.
- The MCP instructions and SKILL.md say to use the default profile and to use a billing profile only when the
  user names it.

- `sdapi` style for the Draw Things app's HTTP API server (`/sdapi/v1/txt2img` and `/img2img`). Strict: only
  `-n` (as `batch_count`), `--size` and `--seed` map, other flags are an error, and `--set` carries the app's own
  keys. A reference becomes `init_images` with width and height taken from the image (PNG or JPEG). Masks are
  rejected. With no seed, imgate picks a random one, because the app otherwise reuses its own seed. Checked
  live, including that an API call overwrites the app's prompt field. See the README.
- Error bodies shaped `{"detail": "..."}` (Draw Things, vLLM-Omni) are now shown. The openai client unwraps the
  `error` key and dropped the detail, leaving `Error code: 422`.

- `extra_in_prompt` profile setting for stable-diffusion.cpp's `sd-server`, which reads only `prompt`, `n`,
  `size`, `output_format` and `output_compression` as top-level fields and silently dropped `--seed`. With
  `extra_in_prompt = true`, `seed` and `--set`/`extra` fields are sent as a `<sd_cpp_extra_args>` block in the
  prompt. Checked live: the same seed gives byte-identical images. A prompt that already holds such a block
  plus `--seed` or `--set` is an error.
- `docs/stable-diffusion-cpp.md`: running FLUX.2 klein 4B locally on a Mac with the prebuilt `sd-server` (Metal).

## 0.5.0 - 2026-11-22

First release checked against a live OpenRouter endpoint. The details are in the
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
- Removed the example `ollama` profile: that server has no image routes. A config written by 0.4.0 `init`
  still has it; delete it.
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
