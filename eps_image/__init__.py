"""EPSNodes' image-utility node family (non-lora).

A sibling of ``lora_library/`` under the EPSNodes pack (FORMAT.md naming note:
the pack is multi-family; lora nodes live in ``lora_library/``, image
workflow utilities here). Holds ``nodes_switcher.py`` (EPS Switcher, §6.4) and
``nodes_resolution.py`` (EPS Resolution, §6.5). No ComfyUI imports at module
scope — everything stays importable/testable without ComfyUI, same convention
as the rest of the pack.
"""
