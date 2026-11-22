# Claude Desktop and Claude Code: Draw Things by default, OpenRouter on approval

Two MCP connectors, one per trust level, so the host app's own approval prompt can guard the paid one. A flag
the model can set itself is not authorization, so none is used.

| Connector | Profiles it can use | Key needed | Intended use |
|---|---|---|---|
| `imgate` | `drawthings` only | none | default, free, local |
| `imgate-openrouter` | `openrouter` only | OpenRouter key, from the macOS Keychain | only when you ask for it, bills money |

`IMGATE_MCP_ALLOWED_PROFILES` (comma list, in the connector's `env`) narrows the profiles one connector may use,
and `IMGATE_PROFILE` sets its default. A call for any other profile is refused before any request is made, and
`list_profiles` shows only the allowed ones. Presets that pin another profile are refused too.

## OpenRouter key in the Keychain

The key never goes into a config file. Store it once, in your own Terminal (the prompt hides what you type):

    security add-generic-password -U -a "$USER" -s imgate-openrouter -w

Launcher, saved as `~/.local/bin/imgate-mcp-openrouter` and made executable:

    #!/bin/sh
    # Starts the imgate MCP server for OpenRouter. The key comes from the macOS Keychain, never from a file.
    KEY=$(/usr/bin/security find-generic-password -s imgate-openrouter -w 2>/dev/null) && export OPENROUTER_API_KEY="$KEY"
    unset KEY
    export IMGATE_PROFILE=openrouter IMGATE_MCP_ALLOWED_PROFILES=openrouter
    exec "$HOME/.venvs/imgate/bin/python" "$HOME/.claude/skills/imgate/imgate.py" serve

Without the Keychain item the server still starts and reports `OPENROUTER_API_KEY is not set` when called. macOS
may ask once whether `security` may read the item. That is a second human gate.

## Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`, then restart)

    "imgate": {
      "command": "/Users/YOU/.venvs/imgate/bin/python",
      "args": ["/Users/YOU/.claude/skills/imgate/imgate.py", "serve"],
      "env": {"IMGATE_PROFILE": "drawthings", "IMGATE_MCP_ALLOWED_PROFILES": "drawthings"}
    },
    "imgate-openrouter": {
      "command": "/Users/YOU/.local/bin/imgate-mcp-openrouter"
    }

Desktop does not inherit your shell, so use absolute paths.

## Claude Code

    claude mcp add imgate -s user -e IMGATE_PROFILE=drawthings -e IMGATE_MCP_ALLOWED_PROFILES=drawthings -- \
      ~/.venvs/imgate/bin/python ~/.claude/skills/imgate/imgate.py serve
    claude mcp add imgate-openrouter -s user -- ~/.local/bin/imgate-mcp-openrouter

## Image to image

Pass the input image in `references`. The MCP tool reads local files only from `output_dir` (default `~/imgate-output`, subfolders included) and `[mcp].reference_dirs`, so that a connector cannot be asked to read `~/.ssh`. Two ways to give it an input:

- Copy the file into `~/imgate-output/` (no config change). Pictures imgate made are already there, so "change the sky in the last one" works as is.
- Or add a folder you choose: `reference_dirs = ["~/Pictures/imgate-inputs"]` under `[mcp]`.

An image dragged into the chat window is not a file on disk for the tool, so it cannot be used as a reference. Whether Desktop exposes attachments to MCP servers is unverified. A path outside the allowed folders gives an error that lists them.

Draw Things notes (checked live 2026-11-22 with Qwen-Image 2.1 turbo selected in the app): the reference is sent as `init_images` and its size becomes the output size. Do not set `strength` for an instruction-edit model: the app default of 1 changed the sky as asked and kept the boat, while 0.3 and 0.8 mostly returned the source.

## What is and is not enforced

- Enforced by imgate: a connector cannot reach a profile outside its list, whatever the model asks for.
- Enforced by the host, not by imgate: the approval prompt. Keep `imgate-openrouter` off "Always allow" so each
  paid call asks you. If you choose "Always allow", that protection is gone. I did not test how Desktop's
  prompts look.
- The `drawthings` profile sends your prompt into the app and overwrites the app's prompt field. Keep the app's
  API server running and do not use this while writing a prompt there.
- Results larger than `[mcp] max_inline_bytes` are saved but not shown inline. Draw Things PNGs at 768x768 are
  0.9 to 1.4 MB, so raise it (for example 3000000). Whether Desktop shows them inline is untested.
