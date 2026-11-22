# Prompting guide for imgate

Read this before writing or revising an image prompt, and use it to coach the user. The spec
structure is adapted from the prompt scaffold in OpenAI's MIT-licensed `imagegen` skill
(openai/skills), rewritten here. The model-specific notes come from each vendor's own docs,
linked at the end with the date checked. Guidance is not a tested recipe: results vary by model,
so compare variants with a fixed seed.

## Quick rules (the cheat sheet to share with a user)

1. Describe the scene in a sentence or two of plain language. A keyword list works worse on
   models that understand language (Google says so for Gemini).
2. Name the subject, the setting, the style or medium, the framing, and the light. Those five
   cover most of what a vague prompt is missing.
3. Say what you want, not what you do not want. "An empty street" beats "no cars".
4. Put text that must appear in quotes, and say where it goes and how it looks.
5. For edits, say what changes and what must stay identical. One change per pass.
6. Label every reference image with its role ("image 1: the subject, image 2: the style").
7. Start cheap: draft on a fast model or a local server, fix the prompt, then spend on the final.
8. Change one thing at a time and keep the same seed, so you know what caused the difference.

## 1. Collect the spec (only the fields that matter)

Write the prompt as a short paragraph, or as labeled lines for complex requests.

- Use case and asset type: hero image, product shot, UI mockup, logo, diagram, scene
- Primary request: one sentence
- Input images: label each one's role
- Scene or backdrop, Subject, Style or medium
- Composition and framing: camera distance and angle, where the subject sits, aspect ratio
- Lighting and mood, Color palette, Materials and textures
- Text to render, verbatim, in quotes
- Constraints that must hold: counts, positions, brand colors
- Exclusions: see section 3

Do not invent details the user did not imply (characters, brands, slogans, palettes). If the
prompt is already detailed, normalize it into this shape and keep the user's wording. If it is
generic, you may suggest composition or lighting, and say which parts are your suggestions.

## 2. Pick the use case

Generation: photorealistic-natural, product-mockup, ui-mockup, infographic-diagram, logo-brand,
illustration-story, stylized-concept, historical-scene.
Edit: text-localization, identity-preserve, precise-object-edit, lighting-weather,
background-extraction, style-transfer, compositing, sketch-to-render.

Use the category to decide what to specify. product-mockup needs surface, lighting and
background. ui-mockup needs layout, screen type and exact label text. infographic-diagram needs
the verbatim text and the structure. historical-scene needs period details stated explicitly.

## 3. Negations and exclusions

Models can render what a prompt names even when it says to leave it out ("no second book").
imgate warns when a prompt contains negation words (quoted text is ignored). Handle it in order:

1. Rewrite positively: "one open Bible, alone in the manger" instead of "no other books".
2. State the count or position that must hold: "exactly one", "centered", "empty background".
3. Use a negative-prompt field only where the model supports it. Black Forest Labs says FLUX.2
   does not support negative prompts, and Google's guidance for Gemini is to describe the scene
   positively. Some other models and servers do accept a `negative_prompt` field
   (`--set negative_prompt="..."`). Check the model's docs first. An unsupported field may be
   ignored silently or rejected.
4. If the model still renders the excluded thing, change the wording or the model. Do not stack
   more negations.

When you rewrite a user's prompt this way, tell them what changed and why.

## 4. Text in the image

Put the exact string in quotes and say where it goes and how it looks ("headline 'OPEN LATE' in
bold white sans-serif across the top third"). Always check the rendered spelling against the request.
If the model garbles it, shorten the text, simplify the typography request, or try another model.

## 5. Edits

- Say what changes and what must not: "Change only the sky to dusk. Keep the building, people,
  framing and colors of everything else identical."
- One edit per pass. Keep the previous file.
- Label every input image by role. With a mask, describe the masked change only.
- If part of an image must stay pixel-identical, do not rely on the prompt. OpenAI's guidance is
  to composite the approved edit back into the original.
- Masks: `--mask` needs a PNG with the same pixel size as the first reference. OpenAI's `mask`
  treats fully transparent areas as the area to edit. OpenRouter's `markup` overlay is documented as
  a transparent PNG that marks where the edit applies, and only some models honor it. The docs do not
  say which pixels mark the area. Observed on `openai/gpt-image-2.6-sunburst`: opaque pixels did
  (2 of 2 trials), and the model re-rendered the whole picture, so treat it as guidance. vLLM-Omni
  uses a `mask_image` field (untested live). Test with a trivial edit first.
- `input_fidelity` and similar knobs are model-specific: `--set input_fidelity=high`.

## 6. Check the result

After each image, check against the request: subject, style, composition, text accuracy, counts,
and every constraint. If something is off, change one thing and rerun with the same `--seed`. Compare
the `.json` sidecars to see exactly what changed.

## 7. Model-specific notes (vendor guidance, checked 2026-11-22)

- **FLUX.2 (Black Forest Labs).** Structure: Subject + Action + Style + Context. No negative
  prompts: replace "no people" with "empty street, deserted scene", and "not blurry" with "sharp
  focus, crisp details". Specify the placement and typography style of quoted text. It also accepts
  structured JSON scene descriptions. A third-party guide shows a `negative_prompt` parameter for
  FLUX.2 [klein], so smaller variants may differ: check the model card.
- **Gemini image models (Google).** Describe the scene narratively instead of listing keywords.
  For realism, think like a photographer: camera angle, lens, lighting, fine detail. Use cinematic
  and photographic terms for composition ("wide-angle", "macro", "low-angle"). Describe what you
  want, not what you do not want. Start with a clear verb ("create an image of ...") or the model
  may answer with text. Ask for the aspect ratio in words when the interface has no setting.
- **OpenAI GPT Image models.** OpenAI publishes a prompting guide for each model generation and
  recommends testing a candidate model on your own prompts before migrating. A third-party summary
  of the OpenAI cookbook reports the ordering background or environment, then subject, then
  specific details, then constraints. Verify that against the cookbook before quoting it.
  Parameter support (quality levels, transparency, `input_fidelity`) changes between model
  generations, so check the current model's reference.
- **Self-hosted diffusion models (vLLM-Omni).** Behavior depends on the model you
  loaded. Ollama is not an option here: it has no image routes. Read that model's card. Treat negation, text rendering and long prompts as untested
  until you try them with a fixed seed.

## 8. Reporting back to the user

Give the saved file path, the final prompt (including any rewrite), profile, model, seed, and cost
if reported. If generation fails, say so. Do not substitute an SVG or HTML drawing for a raster
request unless the user asked for vector output.

## Sources

- Black Forest Labs, FLUX.2 prompting guide: https://docs.bfl.ai/guides/prompting_guide_flux2
- Black Forest Labs help, FLUX.2 negative prompting: https://help.bfl.ai/articles/7734566352-does-flux-2-support-negative-prompting
- Google Cloud, Gemini image generation best practices: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/gemini-image-generation-best-practices
- Google, how to prompt Gemini 2.5 Flash Image: https://developers.googleblog.com/en/how-to-prompt-gemini-2-5-flash-image-generation-for-the-best-results/
- OpenAI, image prompting guide: https://developers.openai.com/api/docs/guides/image-prompting
- OpenAI Cookbook, GPT Image prompting guide: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide
- openai/skills imagegen (MIT), source of the spec scaffold: https://github.com/openai/skills
