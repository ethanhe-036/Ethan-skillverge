# Raster preview generation

Read this reference when the user asks for generated theme previews, concept directions, wallpaper art, or raster variants. A generated concept is not evidence that the live Codex renderer passed QA.

## Capability route

Route by actual capability, not by guessing whether the user appears to have an official-account or API-key login:

1. If the built-in `image_gen` capability is available and no custom proxy provider is selected, use the built-in path.
2. If `config.toml` explicitly selects a provider with a custom `base_url`, or the built-in tool is unavailable under API-key authentication, run `scripts/generate-image2-preview.py --probe`. The probe is offline and reports only fixed source labels plus `hostname[:port]`; it never reports URL userinfo, path, query, credentials, or local executable paths. It accepts only a structurally valid HTTP(S) endpoint with a hostname and valid port, and inserts `/v1` into a root URL's path without moving query or fragment data. Invalid endpoints return the fixed `invalid-base-url` reason. Do not infer a proxy from unselected provider tables. An explicitly selected provider is authoritative: an ambient `OPENAI_BASE_URL` cannot replace its endpoint. With no selected provider, use environment routing only when `OPENAI_API_KEY` and `OPENAI_BASE_URL` form the environment-sourced pair; never attach an auth-file key to an ambient endpoint.
3. When the probe returns `route=proxy-api` or `route=openai-api` with `pass=true`, generate through the bundled wrapper. It applies the `$image2-generate` method: read local Codex config, pass credentials only to the system imagegen CLI child process, and use `gpt-image-2`. This is behavior-level integration; installing the separate user-local `$image2-generate` skill is not required.
4. If neither route is ready, report `NOT_READY` with the wrapper's fixed `notReadyReasons`, such as `missing-api-key`, `missing-imagegen-cli`, `missing-python-runtime`, `unsupported-python-runtime`, `missing-openai`, or a sanitized config/auth parse reason. A missing image endpoint, unsupported `/v1/images/generations` route, unsupported `gpt-image-2` model, or rejected key stays `NOT_READY` until corrected.

If the user explicitly mentions API Key, a proxy, Image2, or CLI generation, their preview request authorizes this configured route. Otherwise, when built-in generation is unavailable, report the sanitized proxy host and ask once before making the first live API call. The offline probe never needs confirmation.

Never print, echo, log, request in chat, or persist an API key. Do not edit `config.toml`, `auth.json`, or the user's environment. Reporting only the configured hostname and optional numeric port is allowed; reporting credentials or URL paths is not.

## Preview workflow

Use two evidence layers and label them separately:

- **Layer A - artwork asset:** use `image_gen` or Image2 for character/background art, mood, crop, material, and safe zones. Avoid generated Codex chrome, controls, labels, code, or other text that could be mistaken for the real product.
- **Layer B - deterministic functional preview:** place the validated Layer A asset and theme tokens into a deterministic Codex workbench fixture, or capture the verified real renderer after authorized application. Exercise representative navigation, composer, code, diff, terminal, approval, menu, and narrow states. Return a raster screenshot, not raw HTML.

A generated mockup or concept is not usability evidence and cannot advance a theme beyond `PARTIAL`. Layer B is still a fixture rather than runtime proof unless it came from the verified renderer; full `PASS` requires [workbench-usability.md](workbench-usability.md) and [surface-qa.md](surface-qa.md).

- Save normalized prompts under `tmp/imagegen/<theme>-<direction>.prompt.txt`.
- For three directions, issue three independent calls so each prompt has an explicit composition, palette, material, crop, and avoid list.
- Use `1536x1024` and `quality=medium` for wide draft previews unless the reference composition needs portrait or square. Use `quality=high` for selected final artwork.
- Save final preview files under `output/imagegen/` with stable descriptive names. Do not overwrite an existing file unless the user requested replacement.
- The proxy wrapper always requests PNG, writes to a sibling staging file, validates chunk order, CRCs, dimensions, the complete compressed pixel stream, and decoded scanlines, then atomically moves the image into place. It rejects truncated, corrupt, trailing, HTML, SVG, or text payloads disguised with an image extension and preserves an existing destination until a valid replacement is ready.
- Independently open each result with `view_image` and check subject, crop, text, privacy, and negative constraints before showing it.
- Use the generated image as concept art or a theme asset only after recording its prompt, model/route, provenance, and usage rights.

Portable proxy command:

```text
<python-3.11+> <skill-root>/scripts/generate-image2-preview.py --prompt-file tmp/imagegen/theme-a.prompt.txt --out output/imagegen/theme-a.png --size 1536x1024 --quality medium
```

Do not hard-code a user profile path. Resolve `<skill-root>` from the loaded `customize-codex-theme` skill and resolve Codex resources through `CODEX_HOME` or `~/.codex`.

The wrapper requires Python 3.11+ for scoped TOML parsing and a child Python with the `openai` SDK. It checks, in order, explicit `--imagegen-python`, `CODEX_IMAGEGEN_PYTHON`, `tmp/imagegen/.venv`, and the current interpreter. Runtime discovery uses a credential-free subprocess; credentials are added only to the final imagegen CLI process. On Windows, use `uv run python` or a known Python 3.11+ executable when the Store `python` alias is unusable. Never silently install packages: when no compatible child runtime exists, report the exact sanitized `NOT_READY` reason and request permission before creating an isolated `tmp/imagegen/.venv` or installing `openai`.

## Output boundary

A request for an image preview requires a real raster deliverable. The proxy wrapper accepts only fully validated PNG; the built-in route may return another raster format when its own decoder verifies it. Never substitute HTML, SVG, CSS, canvas code, or a text-only style description while claiming that an image preview was generated. HTML may be the deliverable only when the user explicitly asks for an interactive prototype; if HTML is used internally to render a deterministic UI composition, capture and return a raster screenshot as the preview.

Generated UI mockups are directional concepts, not screenshots of the real Codex build. Runtime claims still require the adapter and full-surface QA.

## References and failures

- Redact private screenshots before any external service sees them. Do not send account names, tasks, repositories, messages, paths, tokens, or notifications to a proxy.
- The proxy wrapper generates from text prompts. Convert a visual reference into a privacy-safe prompt; use built-in image editing only when actual reference-image fidelity is required and that capability is available.
- For fan art or recognizable media, describe mood, composition, palette, and archetype rather than requesting an exact copyrighted character, logo, costume, or weapon copy. Record licence and provenance.
- Do not silently downgrade from `gpt-image-2` to another model. Report `invalid_api_key`, image-route, and model errors precisely without exposing the configured key.
