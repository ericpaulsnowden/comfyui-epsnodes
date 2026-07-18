# Installing comfyui-lora-library

## Requirements

- ComfyUI (a 2025+ build; the pack is developed against current ComfyUI).
- Nothing else — no pip dependencies.
- Optional: [rgthree-comfy](https://github.com/rgthree/rgthree-comfy), only
  if you want the `LoRA Set Controller` to drive a Power Lora Loader. The
  rest of the pack works without it.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/EPSNodes
```

Restart ComfyUI. You should see `lora_library vX.Y.Z loaded` in the server
log, and a **LoRA Library** section in Settings.

## Point it at your library folder (optional but recommended)

Settings → **LoRA Library** → *Library folder*: an absolute path, e.g.
`D:\comfy-library` or `\\nas\share\comfy-library` (Windows) or
`/Volumes/nas/comfy-library` (macOS). Leave empty to use the per-user
default inside ComfyUI's `user/` directory.

To share one library between machines, point every machine at the same
folder. Concurrent edits are guarded (the pack refuses to overwrite a file
that changed under it and offers reload), but it's a file share, not a
database — one editor at a time is the happy path.

## Update

```bash
cd ComfyUI/custom_nodes/comfyui-lora-library
git pull
```

Restart ComfyUI **and** hard-refresh the browser tab. The versions shown in
Settings → LoRA Library must match; a mismatch means one half is stale.
