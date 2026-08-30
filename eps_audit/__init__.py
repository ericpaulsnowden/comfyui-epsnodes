"""EPS Node Audit — an inventory of what the OTHER custom-node packs on this
server can reach (FORMAT.md §9).

A third family alongside ``lora_library/`` (lora nodes) and ``eps_image/``
(non-lora image nodes), and the only one that looks OUTWARD: ComfyUI custom
nodes are unsandboxed Python running in the server process, free to register
unauthenticated HTTP routes, read any path, and call any host. There is no
permission model to consult and no manifest to read, so the only way to know
what a pack can do is to look — which is what this family does.

**Observe only.** Nothing here blocks, patches, or rewrites another pack's
behaviour. It reads the live route table, records outbound HTTP destinations
as they happen, and scans installed source for a small set of recurring
risky idioms. Every finding is a LEAD TO REVIEW, never a verdict — see
``scanner.py``'s docstring for why the rules are deliberately loose.
"""
