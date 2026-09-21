---
name: imagegen
description: Generate or edit raster images with Codex's built-in image generation tool. Use for image briefs, visual assets, and reference-guided edits.
icon: 🎨
---
# Codex image generation

Use the built-in `image_gen` tool for every image request. It uses the signed-in
Codex allowance and does not need an API key.

- Generate exactly one image unless the brief explicitly asks for variants.
- For an edit, first view every supplied reference image, then tell
  `image_gen` exactly what must change and what must remain fixed.
- Keep prompts concrete: subject, composition, medium, lighting, palette,
  intended use, and anything that must not appear.
- Do not substitute SVG, HTML, or a text description for a requested raster.
- After generation, reply briefly. The runner collects the generated file and
  saves it as a platform artifact automatically.
