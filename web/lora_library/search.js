/**
 * @file Shared search matcher for every LoRA-library / state panel
 * (FORMAT.md §7.2's search-field contract). The pack's first deliberate
 * shared-panel module -- the proof of concept for
 * docs/ROADMAP-shared-panel-code.md's "one implementation, not a fourth
 * copy" rule.
 *
 * Semantics (owner decision 2026-09-08: "use Notebook as the model and
 * make them all follow that paradigm", closing the product question
 * docs/ROADMAP-shared-panel-code.md left open): case-insensitive,
 * whitespace-split, every query word must appear somewhere in the
 * haystack -- AND across words, so a multi-word query narrows rather than
 * broadens. An empty/whitespace-only query matches everything. This is
 * exactly notebook.js's original `entryMatchesSearch`/`searchHaystack`/
 * `searchWords` (v0.53.0, owner ask 2026-08-08), moved here verbatim and
 * now shared by notebook.js, picker.js, universal_controller.js and
 * controller.js instead of being reimplemented (picker.js) or diverging
 * to single-substring (universal_controller.js) or missing entirely
 * (controller.js).
 *
 * Pure functions only -- no DOM, no node/graph references, no imports of
 * its own beyond nothing. Every panel imports these under their EXISTING
 * bare names (`entryMatchesSearch`, `searchHaystack`, `searchWords`) so
 * every CALL-SITE string a test pins on stays byte-identical across the
 * move; only the three DECLARATIONS relocated here.
 */

/**
 * §7.2 search predicate: every word in `words` must appear somewhere in
 * `haystack` (AND across words). An empty `words` array (the query was
 * blank/whitespace) matches everything.
 * @param {string} haystack already-lowercased corpus to search within
 * @param {string[]} words already-lowercased query words
 * @returns {boolean}
 */
export function entryMatchesSearch(haystack, words) {
  return words.every((word) => haystack.includes(word))
}

/**
 * One entry's lowercase haystack: `name\ntext`. A panel with no separate
 * body (picker.js's relPath, a state's plain label) passes `''` for
 * `text`, yielding a name-only haystack with a trailing newline -- safe,
 * since a typed search word can never contain one.
 * @param {string} name @param {string} text
 * @returns {string}
 */
export function searchHaystack(name, text) {
  return `${name}\n${text || ''}`.toLowerCase()
}

/**
 * The query's lowercase words (AND across them via `entryMatchesSearch`);
 * `[]` matches everything.
 * @param {string} query
 * @returns {string[]}
 */
export function searchWords(query) {
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
}
