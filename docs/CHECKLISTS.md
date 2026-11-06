---
name: checklist
description: Create or update the owner's manual-test checklist. Use at the end of any work round that changed behaviour, when the owner reports test results ("item 3 passed", "the drag still jumps"), or when asked to "make / update / show the checklist".
---

# Checklists: how we handle them, and the skill that maintains them

This file does two jobs:

1. **Part 1** describes the process: what a checklist is for in this project,
   what goes on it, and what doesn't.
2. **Part 2** is a skill: step-by-step instructions any LLM agent can follow to
   create or update the checklist.

It is written to be **model-agnostic**. It names no vendor-specific tools. Every
step only needs things any coding agent can do: read a file, write a file, run
a shell command, and reply in chat. The YAML block at the top lets Claude load
it as a skill (copy or symlink it to `.claude/skills/checklist/SKILL.md`). Other
LLMs can ignore that block and read it as plain instructions: paste the file
into the system prompt or a rules file, or point the agent at it.

---

## Part 1: The process

### What a checklist is for

The agent works on its own for long stretches. Before it messages the owner, it
should have proven as much as it can without them: unit tests, a headless
browser, and a real ComfyUI instance (the "rig"). What it **cannot** prove goes
on the checklist, as exact instructions the owner follows by hand.

So the checklist is **not** a list of everything that changed. It covers only
the gap between what the agent verified and what the owner needs to trust.

Typical reasons an item can only be checked by hand (all of these have come up
in this repo):

- **It needs a real pointer or keyboard.** Example: the LoRA Picker split drag
  (v0.78.0). Synthetic pointer capture aborts, so only the layout maths could be
  pinned in tests.
- **It needs the owner's environment.** Examples: a genuinely remote browser
  (Frame Saver Browse hiding, v0.95.0), the NAS library, real checkpoints and
  LoRAs, another machine sharing a workflow.
- **It needs human judgement.** Does it *look* right, is the motion smooth, does
  the image match.
- **It needs a different renderer or version.** The classic canvas vs. the Vue
  "new node design", or the owner's own ComfyUI or frontend version.

### Where it lives

**The single source of truth is `docs/OWNER-CHECKLIST.md`.** The chat message to
the owner is a copy of the open items, not the original.

Why a file: until now, checklist items lived in chat replies and in commit
messages ("flagged for the owner's checklist"). That loses items. A commit
message can't be ticked off, a chat reply scrolls away, and a new session (or a
different LLM) starts with neither. A file in the repo survives sessions,
models and machines, and its git history records when each item was verified.

### What a good item looks like

Each item gets:

| Field | Purpose |
| --- | --- |
| **ID** | Stable, never reused: `v<version>-<n>`, e.g. `v0.95.0-2`. The owner can reply "v0.95.0-2 failed". |
| **Title** | One line saying what is being proven. |
| **Setup** | The state to start from: which workflow and nodes, which environment (remote browser, NAS, Vue renderer). Leave it out if there's nothing to set up. |
| **Steps** | Numbered, concrete actions: what to click, drag or type, and where. |
| **Expect** | One observable result. Something seen, not felt: "the divider stays where you let go", not "it works". |
| **Why manual** | One clause naming what automation couldn't reach. This stops the owner redoing work the agent already did, and shows when an item could be automated later. |
| **Status** | `[ ]` open · `[x]` passed (with date) · `[!]` failed (with the owner's words and a date). |

Rules that keep the list useful:

- **One outcome per item.** If you'd write "and also check…", split it into two
  items.
- **Never "verify it works".** If you can't write the Expect line, you don't
  understand the change well enough to ask the owner to test it.
- **Check the version first.** Each version heading opens with a line telling
  the owner to confirm the version shown in the app (here: Settings →
  EPSNodes) matches the heading. This catches
  testing a stale install, the most common false failure.
- **Group items by shippable feature, riskiest first.** Each group can be tested
  and signed off on its own. A failure in one group shouldn't block the others.
- **Say what's already proven.** Each group gets one line on what the agent
  verified (tests, rig), so the owner tests only the gap.
- **Keep it short.** If a round produces more than about 10 items, more of it
  should have been automated. Push back on yourself before you push the work
  onto the owner.

### Item lifecycle

```
open [ ] ──owner: passed──▶ [x] passed (date)
    │
    └──owner: failed──▶ [!] failed (date, owner's words)
                            │
                            └─ fix ships in vX.Y.Z ─▶ new item vX.Y.Z-n "re-test of <old ID>"
                                                      old item gets "→ re-test: <new ID>"
```

- Items are **never deleted**. The record is the point.
- A failed item is never flipped to passed by a fix. The fix earns a **new**
  item that refers back to the old one, so the history shows that a fix was
  needed and when.
- Once every item in a release group has passed, move the whole group under
  `## Verified`, newest first. Open work stays at the top.

---

## Part 2: The skill

### When to run it

- **Create / add**: at the end of a work round that changed behaviour, before
  the agent messages the owner. Also mid-round when the agent hits something it
  can't prove; add it then, so it isn't lost.
- **Update**: when the owner reports results in any form: "1 and 3 pass, 2 is
  broken", "the drag thing works now", a screenshot, a bug report that matches
  an open item.
- **Show**: when asked "what's left to test?" or similar.

### Inputs

- What changed this round: the diff, commit messages, version numbers.
- What the agent already verified, and how.
- For updates, the owner's report, word for word.

### Procedure: create or add items

1. **Read `docs/OWNER-CHECKLIST.md`.** If it doesn't exist, create it from the
   template below.
2. **Read the app version** from wherever the project keeps it (here:
   `pyproject.toml`, and the version shown in **Settings → EPSNodes**). Every
   new item's ID uses that version, and each version heading starts with a
   "First: check the version" line.
3. **List the candidate items.** For each behaviour change this round, ask:
   *"Did I observe this exact outcome myself, in a real environment?"* If yes,
   it goes on the "already proven" line, not on the checklist. If no, it's a
   candidate.
4. **Challenge each candidate.** Could a test, a headless browser, or the rig
   prove it? If yes and it's cheap, go do that instead. Only what's left goes
   on the list. Record *why* in the item's "Why manual" field.
5. **Write the items** in the format above, under a heading for this version and
   feature. Number them from 1 within the version. Never renumber existing
   items.
6. **Check for duplicates.** If an open item already covers the same behaviour,
   update it (new Setup or Steps, note the version) instead of adding a second
   one.
7. **Save the file and commit it** with the code it describes, in the same
   commit or the one straight after, so the checklist never describes code that
   isn't pushed.
8. **Message the owner** with the open items copied from the file (ID, steps,
   expect), grouped by feature, and one sentence on what to reply with. Don't
   paraphrase in chat: the file and the message must match.

### Procedure: update from an owner report

1. **Read `docs/OWNER-CHECKLIST.md`.**
2. **Match each statement in the report to an item ID.** If one statement could
   mean more than one item, ask, and give the candidate IDs as choices. Never
   guess a pass.
3. **For each match:**
   - Passed → `[x]` plus today's date (`YYYY-MM-DD`).
   - Failed → `[!]` plus the date, and the owner's words quoted exactly. Don't
     soften them into "minor issue".
   - Partly works → that's a failure. Record exactly which part failed.
4. **Report anything that matches no item** as a new finding. It means
   something was missed. Treat it as a bug report, not a checklist update.
5. **Move fully passed release groups** under `## Verified`.
6. **Save and commit** with a message like
   `checklist: v0.95.0-1,-3 passed; v0.95.0-2 failed`.
7. **Reply** with: what's now passed, what failed and what you'll do about each
   failure, and what's still open. If a failure gets fixed in this session, the
   fix's round adds the re-test item (see the lifecycle above).

### Procedure: show

Read the file and reply with the open `[ ]` and failed `[!]` items only,
grouped by version, with a count at the top. Don't change the file.

### Template for `docs/OWNER-CHECKLIST.md`

```markdown
# Owner checklist

Manual tests that automation could not cover. Source of truth; maintained per
docs/CHECKLISTS.md. Reply with item IDs, e.g. "v0.95.0-1 pass, v0.95.0-2 fail: <what you saw>".

## Open

### v0.95.0 — Frame Saver: upload from a remote browser

Already proven: 3934 tests; on the rig, a real upload ran the node and decoded
the frame at 320x180; the Upload button renders.

First: Settings → EPSNodes shows 0.95.0 for both backend and frontend. If not,
update, restart and hard-refresh before testing anything.

- [ ] **v0.95.0-1 — Browse is hidden on a remote browser**
  Setup: open ComfyUI from a *different* machine than the server.
  Steps: 1. Add an EPS Frame Saver. 2. Look at the buttons row.
  Expect: Upload is shown; Browse is not.
  Why manual: the rig is local; "remote" can't be faked honestly.

## Verified

### v0.78.0 — LoRA Picker: draggable split

- [x] **v0.78.0-1 — The divider drags and stays put** (2026-09-02)
  Steps: 1. Drag the divider between Selected and browse. 2. Let go. 3. Switch tabs and back.
  Expect: the divider stays where you let go, after the tab switch too.
  Why manual: needs a real pointer; synthetic capture aborts.
```

(The example entries show the format. Their dates and statuses are made up.)

### Checks before you finish

- [ ] Every new item has an ID, Steps, an Expect line and a Why manual line.
- [ ] No item says "works", "correctly" or "as expected" without saying what
      that looks like.
- [ ] No item covers something you already observed yourself.
- [ ] The chat message and the file list the same open items.
- [ ] The file is committed.
