# Local FLUX.2 klein on a Mac with stable-diffusion.cpp

How to run FLUX.2 klein 4B locally on Apple silicon and use it from imgate through an OpenAI-style endpoint.
Checked live on 2026-11-22 on a 64 GB M-series Mac with `sd-server` build `master-1106-532d2c1`. Sources:
stable-diffusion.cpp's `README.md`, `docs/models/flux2.md`, `docs/build.md`, `examples/server/README.md` and
`examples/server/api.md`.

## Why this server

- It supports FLUX.2-klein (4B and 9B) and serves the OpenAI images API at `/v1`: `POST /v1/images/generations`,
  `POST /v1/images/edits` and `GET /v1/models` (which lists one fixed id, `sd-cpp-local`).
- There is no Homebrew formula or cask. The project publishes a prebuilt macOS zip with every release, so no
  `cmake` build is needed.
- Ollama is not an alternative for this: its OpenAI-compatible API has no image routes.

## Install

The binary release is universal (arm64 and x86_64), unsigned, and not managed by Homebrew, so new versions are
manual downloads. The release job builds without `-DSD_METAL=ON`, but ggml turns Metal on by default on Apple
platforms. Confirmed: the binary links `Metal.framework`, `./sd-server --list-devices` initializes a Metal
device, and the server log shows the model buffers on `MTL0`.

All model files below are Apache-2.0 and need no Hugging Face login. Total 16.1 GB.

    gh release download master-1106-532d2c1 -R leejet/stable-diffusion.cpp -p '*Darwin*arm64.zip'   # then unzip
    R=https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/main/split_files
    curl -L -C - -o flux2_ae.safetensors        $R/vae/flux2-vae.safetensors                       # 0.34 GB
    curl -L -C - -o flux-2-klein-4b.safetensors $R/diffusion_models/flux-2-klein-4b.safetensors    # 7.75 GB
    curl -L -C - -o qwen_3_4b.safetensors       $R/text_encoders/qwen_3_4b.safetensors             # 8.04 GB

## Run

    ./sd-server --listen-ip 127.0.0.1 --listen-port 1234 --model-dir . \
      --diffusion-model flux-2-klein-4b.safetensors --vae flux2_ae.safetensors --llm qwen_3_4b.safetensors \
      --cfg-scale 1.0 --steps 4

`--listen-ip` defaults to `127.0.0.1`, so it is loopback only. `--help` shows no API key option. Startup is
fast because weights load lazily on the first request (about 8 s of that first call).

## imgate profile

    [profiles.sdcpp]
    style           = "openai"
    base_url        = "http://127.0.0.1:1234/v1"
    model           = "sd-cpp-local"
    extra_in_prompt = true

    imgate.py generate "..." -P sdcpp --size 768x768 --seed 7
    imgate.py generate "Change only the sky to dusk" -P sdcpp --ref ./input.png --seed 7

### Why `extra_in_prompt = true`

The server reads only `prompt`, `n`, `size`, `output_format` and `output_compression` as top-level fields (plus
`image[]` and `mask` on edits). It ignores anything else without an error. imgate 0.5.0 sent `--seed` as a body
field, and the server log showed a random seed (364679047), not the requested 7.

The server's documented way to pass other settings is a `<sd_cpp_extra_args>{json}</sd_cpp_extra_args>` block
inside the prompt, using the field names of its native API (`seed`, `sample_params.sample_steps`, ...). With
`extra_in_prompt = true`, imgate moves `seed` and every `--set` or `extra` field into that block. Example:
`--seed 7 --set 'sample_params={"sample_steps":4}'` sends
`PROMPT <sd_cpp_extra_args>{"sample_params":{"sample_steps":4},"seed":7}</sd_cpp_extra_args>`.
A prompt that already contains such a block, combined with `--seed` or `--set`, is an error.

## Results

- Text-to-image at 768x768: 37 to 51 s (the 51 s run included loading the weights). The picture matched the
  prompt.
- Image edit through `/v1/images/edits` with a reference image: 84 s. The sky changed and the subject was kept.
- Seed: two runs with the seed sent through the tag gave byte-identical PNGs, and the log showed `seed 7`.
  imgate's own `--seed` before the fix gave a random seed.

Not tested: `mask` on edits, and imgate fields the server does not list (`--aspect-ratio`, `--quality`,
`--background`), which the docs suggest are ignored.

## Remove it

    kill $(pgrep -f sd-server)
    rm -rf <the folder holding the binary and the three model files>

and delete the `[profiles.sdcpp]` block from `~/.config/imgate/config.toml`.
