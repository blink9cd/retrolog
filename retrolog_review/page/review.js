// The retrolog review page: a section and a choice per title retrolog_resolve
// could not settle alone, and a button saving the choices as picks.json.
//
// A classic script, not a module: retrolog_review inlines it into a plain
// <script> so the page runs from file://, where a module would be refused. It
// reads the `review-data` JSON block, keeps each choice in localStorage as it
// is made (until Forget stored picks removes them), and makes no request of
// any kind. The page's fixed text is in review.html.
// Its helpers are exposed, frozen, as `globalThis.RetrologReview` for the
// suites; `boot` is the only line that runs on load. The types its JSDoc names
// (Entry, Answer, Slot and the rest) are declared in review_types.d.ts. The
// storage helpers come from review_storage.js, inlined ahead of this file.
"use strict";

(function (root) {
  const { forgetAnswers, isAnswer, isPlainObject } = root.RetrologStorage;
  const { loadAnswers, openStorage, saveAnswers, storageKey } =
    root.RetrologStorage;
  // The kinds whose resolve result stands unless the operator changes it.
  const KEEP_KINDS = ["most-voted", "variant", "re-searched", "no-isbn"];
  // NUL cannot occur in a title, so medium, title and row never run together.
  const KEY_SEPARATOR = "\u0000";
  // A TMDb id: resolve_columns.TMDB_ID, which a Python test pins this to.
  const TMDB_ID = /^[1-9][0-9]{0,9}$/;
  const WORK_ID = /^\/works\/OL\d+W$/;
  // The ISBN forms resolve's isbn13_or_none accepts: an ISBN-13 of 978 or 979,
  // or an ISBN-10 whose check character alone may be X. Each must also carry
  // its check digit, or --mode overrides would refuse the saved picks. A
  // Python test pins these and the arithmetic below to resolve_isbn's.
  const ISBN13 = /^97[89][0-9]{10}$/;
  const ISBN10 = /^[0-9]{9}[0-9X]$/;
  // Over every character, check included: ISBN-13 weighs them 1, 3, 1, ...
  // modulo 10, ISBN-10 ten down to one modulo 11, X standing for ten.
  const ISBN13_WEIGHTS = [1, 3];
  const ISBN13_MODULUS = 10;
  const ISBN10_MODULUS = 11;
  const ISBN10_X = 10;
  const ISBN_NOISE = /[-\s]/g;
  const TMDB_MOVIE = "https://www.themoviedb.org/movie/";
  const OPEN_LIBRARY = "https://openlibrary.org";
  const CANDIDATE = "candidate-";
  const PICKS_FILE = "picks.json";
  const PICKS_SCHEMA = 1;
  // The data block's schema, review_read.PAGE_SCHEMA.
  const PAGE_SCHEMA = 1;
  const BAD_FILM_ID =
    "Not a TMDb id: type 1 to 10 digits with no leading zero.";
  const BAD_BOOK_ID =
    "Not a work id or ISBN: type /works/OL…W, or 10 or 13 digits.";

  /**
   * An entry's storage key: its medium, title and first row. resolve groups
   * rows by both years too, so one title can make several entries; no row is
   * in two of them, so the first row tells them apart.
   * @param {{medium: string, title: string, rows: string[]}} entry
   */
  function entryKey(entry) {
    return [entry.medium, entry.title, entry.rows[0]].join(KEY_SEPARATOR);
  }

  /** `n word`, with an s unless n is 1. @param {number} n @param {string} word */
  function countOf(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  /** What an entry starts on: keep, or undecided (null). @param {Entry} entry */
  function startingAnswer(entry) {
    return KEEP_KINDS.includes(entry.kind) ? "keep" : null;
  }

  /** Whether bare ISBN text carries its check digit. @param {string} bare */
  function isValidIsbn(bare) {
    const digits = [...bare].map((c) => (c === "X" ? ISBN10_X : Number(c)));
    const sum = (/** @type {(at: number) => number} */ weight) =>
      digits.reduce((total, digit, at) => total + weight(at) * digit, 0);
    if (ISBN13.test(bare)) {
      const weights = (/** @type {number} */ at) => ISBN13_WEIGHTS[at % 2];
      return sum(weights) % ISBN13_MODULUS === 0;
    }
    const weights = (/** @type {number} */ at) => bare.length - at;
    return ISBN10.test(bare) && sum(weights) % ISBN10_MODULUS === 0;
  }

  /**
   * Classify a typed id. A film's is a TMDb id; a book's is a work id, or an
   * ISBN of ten or thirteen digits once hyphens and spaces are dropped, its
   * check digit correct.
   * @param {string} medium @param {string} text
   * @returns {IdAnswer | null} null when the text is no valid id.
   */
  function classifyTypedId(medium, text) {
    const id = text.trim();
    const isbn = id.replace(ISBN_NOISE, "").toUpperCase();
    if (medium === "film") return TMDB_ID.test(id) ? { tmdb_id: id } : null;
    if (WORK_ID.test(id)) return { ol_work: id };
    return isValidIsbn(isbn) ? { isbn13: isbn } : null;
  }

  /**
   * The answer picking `candidate` gives; a book's ISBN only if it has one.
   * @param {string} medium @param {Candidate} candidate @returns {IdAnswer}
   */
  function candidateAnswer(medium, candidate) {
    if (medium === "film") return { tmdb_id: candidate.id };
    if (!candidate.isbn13) return { ol_work: candidate.id };
    return { ol_work: candidate.id, isbn13: candidate.isbn13 };
  }

  /**
   * A candidate's catalog page, or null with no usable id. The id must be its
   * catalog's (a film's TMDb id, a book's work key), so the link can never be
   * turned toward another host or path.
   * @param {string} medium @param {string} id @returns {string | null}
   */
  function catalogUrl(medium, id) {
    if (medium !== "film") return WORK_ID.test(id) ? OPEN_LIBRARY + id : null;
    return TMDB_ID.test(id) ? TMDB_MOVIE + id : null;
  }

  /** An answer as text, its keys sorted, so two compare. @param {Answer} a */
  function canonical(a) {
    return JSON.stringify(a, Object.keys(a).sort());
  }

  /**
   * Whether `entry`'s own choices can show `answer`: Keep only where the
   * entry offers it, an id pair only as a candidate's, and a single id as a
   * candidate's or as the id field would take it typed.
   * @param {Entry} entry @param {Answer} answer
   */
  function fitsEntry(entry, answer) {
    if (answer === "keep") return startingAnswer(entry) === "keep";
    if (answer.unresolvable) return true;
    const same = (/** @type {Answer | null} */ other) =>
      other !== null && canonical(other) === canonical(answer);
    const { medium, candidates } = entry;
    if (candidates.some((c) => same(candidateAnswer(medium, c)))) return true;
    const ids = Object.values(answer);
    return ids.length === 1 && same(classifyTypedId(medium, String(ids[0])));
  }

  /**
   * Drop each stored answer its entry cannot show, so an edited store can
   * neither stop the page rendering nor reach the picks file.
   * @param {Entry[]} entries @param {Answers} answers
   */
  function dropUnfit(entries, answers) {
    for (const entry of entries) {
      const key = entryKey(entry);
      const answer = /** @type {Answer} */ (answers.get(key));
      if (answers.has(key) && !fitsEntry(entry, answer)) answers.delete(key);
    }
  }

  /**
   * The answer in force: the operator's, else the entry's starting one.
   * @param {Entry} entry @param {Answers} answers @returns {Answer | null}
   */
  function answerFor(entry, answers) {
    const key = entryKey(entry);
    if (!answers.has(key)) return startingAnswer(entry);
    return /** @type {Answer | null} */ (answers.get(key));
  }

  /**
   * picks.json's object: one pick per decided entry, in entry order, each
   * naming its entry by medium, title and rows (in the data block's order).
   * @param {ReviewData} data @param {Answers} answers
   */
  function buildPicks(data, answers) {
    const picks = [];
    for (const entry of data.entries) {
      const answer = answerFor(entry, answers);
      if (answer === null) continue;
      const { medium, title, rows } = entry;
      picks.push({ medium, title, rows: [...rows], answer });
    }
    return { schema: PICKS_SCHEMA, review_sha256: data.review_sha256, picks };
  }

  /**
   * The page's review, or null when its data block cannot be read or names a
   * schema other than PAGE_SCHEMA. The bare template's `{}` reads as a review
   * with nothing in it.
   * @param {Document} doc @returns {ReviewData | null}
   */
  function readData(doc) {
    const block = doc.getElementById("review-data");
    let raw;
    try {
      raw = JSON.parse(block === null ? "" : String(block.textContent));
    } catch {
      return null;
    }
    if (!isPlainObject(raw)) return null;
    const bare = Object.keys(raw).length === 0;
    if (!bare && raw.schema !== PAGE_SCHEMA) return null;
    const { errors = 0, applied = 0 } = raw.not_shown || {};
    const { review_sha256 = "", entries = [] } = raw;
    return { review_sha256, not_shown: { errors, applied }, entries };
  }

  /**
   * A new element with attributes and optional text.
   * @param {Document} doc @param {string} tag
   * @param {Record<string, string>} attrs @param {string} [text]
   */
  function make(doc, tag, attrs, text) {
    const node = doc.createElement(tag);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** What `selector` names in `scope`. @param {ParentNode} scope @param {string} selector */
  function find(scope, selector) {
    // Every selector here names an element the page itself always holds.
    return /** @type {HTMLInputElement} */ (scope.querySelector(selector));
  }

  /** A radio choice and its label. @param {Slot} slot @param {string} value */
  function radioRow(slot, value, /** @type {string} */ text = "") {
    const doc = slot.page.doc;
    const id = "pick-" + slot.index + "-" + value;
    const name = "pick-" + slot.index;
    const row = make(doc, "div", { class: "choice" });
    const label = make(doc, "label", { for: id }, text);
    row.append(make(doc, "input", { type: "radio", name, value, id }), label);
    return { row, label };
  }

  /** A candidate's year, creator and votes, where known. @param {Candidate} c */
  function candidateFacts(c) {
    const votes = typeof c.votes === "number" ? countOf(c.votes, "vote") : "";
    const year = c.year ? String(c.year) : "";
    return [year, c.creator, votes].filter(Boolean).join(", ");
  }

  /** A candidate's row: its facts, Suggested on the first, and its link. */
  function candidateRow(/** @type {Slot} */ slot, /** @type {number} */ at) {
    const { doc } = slot.page;
    const { medium } = slot.entry;
    const candidate = slot.entry.candidates[at];
    const { row, label } = radioRow(slot, CANDIDATE + at);
    const span = (/** @type {string} */ cls, /** @type {string} */ text) =>
      label.append(make(doc, "span", { class: cls }, text));
    row.classList.add("candidate");
    span("cand-title", candidate.title);
    if (at === 0) span("suggested", "Suggested");
    const original = candidate.original_title;
    if (original && original !== candidate.title) {
      span("cand-original", "Original title: " + original);
    }
    const facts = candidateFacts(candidate);
    if (facts) span("cand-facts", facts);
    const href = catalogUrl(medium, candidate.id);
    if (href === null) return row;
    const site = medium === "film" ? "TMDb" : "Open Library";
    const attrs = { class: "catalog-link", href, target: "_blank" };
    const link = { ...attrs, rel: "noopener noreferrer" };
    row.append(make(doc, "a", link, "View on " + site));
    return row;
  }

  /** The "Type an id" row: its radio, the id field, and the field's message. */
  function typedRow(/** @type {Slot} */ slot) {
    const doc = slot.page.doc;
    const { row } = radioRow(slot, "typed", "Type an id");
    const id = "typed-" + slot.index;
    const messageId = id + "-message";
    const film = slot.entry.medium === "film";
    const what = film ? "TMDb id" : "Open Library work id or ISBN";
    const field = { type: "text", class: "typed-id", id, autocomplete: "off" };
    const more = { spellcheck: "false", "aria-describedby": messageId };
    const message = { class: "field-message", id: messageId };
    row.classList.add("typed");
    row.append(
      make(doc, "label", { for: id, class: "field-label" }, what),
      make(doc, "input", { ...field, ...more }),
      make(doc, "p", { ...message, "aria-live": "polite" }),
    );
    return row;
  }

  /** The typed id's answer, marking the field and its message when invalid. */
  function typedAnswer(/** @type {Slot} */ slot) {
    const { medium } = slot.entry;
    const field = find(slot.part, "input.typed-id");
    const answer = classifyTypedId(medium, field.value);
    const invalid = answer === null && field.value.trim() !== "";
    const bad = medium === "film" ? BAD_FILM_ID : BAD_BOOK_ID;
    find(slot.part, ".field-message").textContent = invalid ? bad : "";
    if (invalid) field.setAttribute("aria-invalid", "true");
    else field.removeAttribute("aria-invalid");
    return answer;
  }

  /** The checked radio's answer. @param {Slot} slot @returns {Answer | null} */
  function choiceAnswer(slot) {
    const value = find(slot.part, "input[type=radio]:checked").value;
    const { medium, candidates } = slot.entry;
    if (value === "keep") return "keep";
    if (value === "none") return { unresolvable: true };
    if (value === "typed") return typedAnswer(slot);
    const at = Number(value.slice(CANDIDATE.length));
    return candidateAnswer(medium, candidates[at]);
  }

  /**
   * Re-draw progress and decided marks; with `filter`, re-apply "Show only
   * undecided" too, which typing skips so an entry cannot vanish mid-word.
   * @param {Page} page @param {Slot[]} slots @param {boolean} filter
   */
  function refresh(page, slots, filter) {
    const only = find(page.doc, "#only-undecided").checked;
    let decided = 0;
    for (const { entry, part } of slots) {
      const isDecided = answerFor(entry, page.answers) !== null;
      if (isDecided) decided += 1;
      part.classList.toggle("decided", isDecided);
      if (filter) part.hidden = only && isDecided;
    }
    const progress = decided + " of " + slots.length + " decided";
    find(page.doc, "#progress").textContent = progress;
  }

  /**
   * Record a slot's current choice, store it, and re-draw.
   * @param {Slot} slot @param {Slot[]} slots @param {boolean} filter
   */
  function onChoice(slot, slots, filter) {
    const { page } = slot;
    const { storage, data, answers } = page;
    answers.set(entryKey(slot.entry), choiceAnswer(slot));
    const saved = saveAnswers(storage, data.review_sha256, answers);
    // Shown on the first failure and never hidden again: picks already made
    // are no safer for a later write succeeding.
    if (!saved) find(page.doc, "#storage-notice").hidden = false;
    refresh(page, slots, filter);
  }

  /** Check the radio an answer selects; a typed one also refills the field. */
  function restoreChoice(/** @type {Slot} */ slot) {
    const { entry } = slot;
    const answer = answerFor(entry, slot.page.answers);
    if (answer === null) return;
    const matches = (/** @type {Candidate} */ c) =>
      canonical(candidateAnswer(entry.medium, c)) === canonical(answer);
    const at = entry.candidates.findIndex(matches);
    let value = at >= 0 ? CANDIDATE + at : "typed";
    if (answer === "keep") value = "keep";
    else if (answer.unresolvable) value = "none";
    else if (value === "typed") {
      const typed = answer.tmdb_id ?? answer.ol_work ?? answer.isbn13;
      find(slot.part, "input.typed-id").value = String(typed);
    }
    find(slot.part, 'input[value="' + value + '"]').checked = true;
  }

  /** An entry's section: its heading, facts, and fieldset of choices. */
  function renderEntry(/** @type {Slot} */ slot) {
    const { doc } = slot.page;
    const { entry, part } = slot;
    const meta = "Kind: " + entry.kind + ", medium: " + entry.medium;
    const rows = "Rows: " + entry.rows.join(", ");
    part.append(make(doc, "h2", {}, entry.title));
    part.append(make(doc, "p", { class: "entry-meta" }, meta));
    part.append(make(doc, "p", { class: "entry-rows" }, rows));
    if (entry.detail) {
      part.append(make(doc, "p", { class: "entry-detail" }, entry.detail));
    }
    const set = make(doc, "fieldset", {});
    set.append(make(doc, "legend", {}, "Pick for " + entry.title));
    if (startingAnswer(entry)) set.append(radioRow(slot, "keep", "Keep").row);
    entry.candidates.forEach((_, at) => set.append(candidateRow(slot, at)));
    set.append(typedRow(slot), radioRow(slot, "none", "None of these").row);
    part.append(set);
    restoreChoice(slot);
  }

  /**
   * Wire a rendered slot: any change records the choice and re-filters;
   * typing selects "Type an id" and records without re-filtering.
   * @param {Slot} slot @param {Slot[]} slots
   */
  function wireEntry(slot, slots) {
    const { part } = slot;
    part.addEventListener("change", () => onChoice(slot, slots, true));
    // Typing in the field is choosing it, so the radio follows the keyboard.
    find(part, "input.typed-id").addEventListener("input", () => {
      find(part, 'input[value="typed"]').checked = true;
      onChoice(slot, slots, false);
    });
  }

  /** Fill and show the not-shown line when anything was left off the page. */
  function renderNotShown(/** @type {Page} */ page) {
    const { errors, applied } = page.data.not_shown;
    if (errors + applied === 0) return;
    const counts = [
      countOf(errors, "error"),
      countOf(applied, "applied override"),
    ];
    find(page.doc, "#not-shown-counts").textContent = counts.join(" and ");
    find(page.doc, "#not-shown").hidden = false;
  }

  /** Save picks.json through a blob link, then let the URL go. */
  function savePicks(/** @type {Page} */ page) {
    const { doc, win } = page;
    const picks = buildPicks(page.data, page.answers);
    const text = JSON.stringify(picks, null, 2) + "\n";
    const blob = new win.Blob([text], { type: "application/json" });
    const href = win.URL.createObjectURL(blob);
    const link = make(doc, "a", { href, download: PICKS_FILE, hidden: "" });
    doc.body.append(link);
    link.click();
    link.remove();
    // Revoked after this turn rather than inside it: a browser may start
    // reading the blob only once the click has been handled.
    win.setTimeout(() => win.URL.revokeObjectURL(href), 0);
  }

  /**
   * Forget this review's stored picks and store no more from this page view.
   * The picks already made stay on the page, so Save picks file still holds
   * them; the storage notice says they will not survive a reload.
   */
  function forgetPicks(/** @type {Page} */ page) {
    const { doc } = page;
    const forgotten = forgetAnswers(page.storage, page.data.review_sha256);
    // A null storage makes every later save fail, which is what keeps the
    // next pick from storing everything again.
    page.storage = null;
    find(doc, "#forgotten").hidden = !forgotten;
    find(doc, "#forget-failed").hidden = forgotten;
    find(doc, "#storage-notice").hidden = false;
  }

  /** Render the review into `doc` and wire its controls. */
  function init(/** @type {Document} */ doc, /** @type {PageWindow} */ win) {
    const data = readData(doc);
    if (data === null) {
      find(doc, "#unreadable").hidden = false;
      return;
    }
    const storage = openStorage(win);
    const { answers, ok } = loadAnswers(storage, data.review_sha256);
    dropUnfit(data.entries, answers);
    /** @type {Page} */
    const page = { doc, win, data, storage, answers };
    find(doc, "#storage-notice").hidden = ok;
    renderNotShown(page);
    const slots = data.entries.map((entry, index) => {
      const part = make(doc, "section", { class: "entry" });
      return { page, entry, index, part };
    });
    slots.forEach((slot) => renderEntry(slot));
    slots.forEach((slot) => wireEntry(slot, slots));
    find(doc, "#entries").append(...slots.map((slot) => slot.part));
    find(doc, "#empty").hidden = slots.length > 0;
    refresh(page, slots, true);
    const toggle = find(doc, "#only-undecided");
    toggle.addEventListener("change", () => refresh(page, slots, true));
    const save = find(doc, "#save-picks");
    save.addEventListener("click", () => savePicks(page));
    const forget = find(doc, "#forget-picks");
    forget.addEventListener("click", () => forgetPicks(page));
  }

  /** Run `init` where there is a page; false where there is none. */
  function boot(/** @type {{document?: Document}} */ scope) {
    if (scope.document === undefined) return false;
    const win = /** @type {PageWindow} */ (/** @type {unknown} */ (scope));
    init(scope.document, win);
    return true;
  }

  const api = Object.freeze({
    boot,
    buildPicks,
    candidateAnswer,
    catalogUrl,
    classifyTypedId,
    countOf,
    entryKey,
    forgetAnswers,
    init,
    isAnswer,
    loadAnswers,
    openStorage,
    readData,
    saveAnswers,
    startingAnswer,
    storageKey,
  });
  Object.defineProperty(root, "RetrologReview", { value: api });
  boot(root);
})(globalThis);
