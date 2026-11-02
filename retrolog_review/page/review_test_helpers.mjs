// Shared scaffolding for the review page suites.
//
// review_storage.js and review.js are classic scripts with no exports, so
// they are run here the way a browser runs them: compiled as scripts, in page
// order, each under its own file name so node's coverage counts it as itself.
// review.js's namespace is then read off the global object. In node there is
// no `document`, so its own boot does nothing and each test calls `init` with
// a jsdom document and a window of its choice.
//
// Every title, person and id below is invented.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import { JSDOM } from "jsdom";

const PAGE_DIR = dirname(fileURLToPath(import.meta.url));
// The page's scripts, in the order retrolog_review inlines them.
const SCRIPT_PATHS = ["review_storage.js", "review.js"].map((name) =>
  join(PAGE_DIR, name),
);
export const TEMPLATE_PATH = join(PAGE_DIR, "review.html");
// What the page writer inlines: every script, in order, joined by a newline.
export const SCRIPT_SOURCE = SCRIPT_PATHS.map((path) =>
  readFileSync(path, "utf8"),
).join("\n");
export const TEMPLATE = readFileSync(TEMPLATE_PATH, "utf8");

// A non-opaque origin, so jsdom offers localStorage. jsdom loads no
// subresource by default, so nothing is fetched from it.
const PAGE_URL = "http://localhost/review.html";

// The template's data block, exactly as the page writer finds it.
const DATA_BLOCK =
  '<script type="application/json" id="review-data">{}</script>';

/** Run the page's scripts as classic scripts; return review.js's namespace. */
export function loadReview() {
  for (const path of SCRIPT_PATHS) {
    vm.runInThisContext(readFileSync(path, "utf8"), { filename: path });
  }
  return globalThis.RetrologReview;
}

export const SHA = "a".repeat(64);

/** One candidate with every field, overridable. */
export function candidate(overrides) {
  return {
    title: "Placeholder Film Two",
    original_title: "",
    year: 1927,
    creator: "Invented Director",
    id: "1000003",
    isbn13: "",
    votes: 120,
    ...overrides,
  };
}

/** The review data block with one entry of every decidable kind. */
export function sampleData() {
  return {
    schema: 1,
    review_sha256: SHA,
    medium: "all",
    not_shown: { errors: 1, applied: 4 },
    entries: [
      {
        kind: "ambiguous",
        medium: "film",
        title: "Placeholder Film Two",
        rows: ["2010/seen.txt:4", "2011/seen.txt:9"],
        detail: "2 exact titles",
        candidates: [
          candidate({ original_title: "Invented Original Two" }),
          candidate({ id: "1000004", year: null, votes: null, creator: "" }),
        ],
      },
      {
        kind: "not-found",
        medium: "book",
        title: "Placeholder Book One",
        rows: ["2012/read.txt:1"],
        detail: "",
        candidates: [],
      },
      {
        kind: "most-voted",
        medium: "film",
        title: "Placeholder Film Three",
        rows: ["2013/seen.txt:2"],
        detail: "most votes of 3",
        candidates: [candidate({ title: "Placeholder Film Three", votes: 1 })],
      },
      {
        kind: "variant",
        medium: "book",
        title: "Placeholder Book Two",
        rows: ["2014/read.txt:3"],
        detail: "matched a variant title",
        candidates: [
          candidate({
            title: "Placeholder Book Two",
            creator: "Invented Author",
            id: "/works/OL100W",
            isbn13: "9780000000002",
            votes: null,
          }),
        ],
      },
      {
        kind: "no-isbn",
        medium: "book",
        title: "Placeholder Book Three",
        rows: ["2015/read.txt:5"],
        detail: "no edition carries an ISBN",
        candidates: [
          candidate({
            title: "Placeholder Book Three",
            id: "/works/OL200W",
            votes: null,
          }),
        ],
      },
    ],
  };
}

/** The template with `data` embedded the way the page writer embeds it. */
export function pageHtml(data, extra = "") {
  const json = JSON.stringify(data).replaceAll("<", "\\u003c");
  const block = DATA_BLOCK.replace("{}", json);
  return TEMPLATE.replace(DATA_BLOCK, block).replace(
    "</body>",
    `${extra}</body>`,
  );
}

/** A jsdom page holding `data`, with no script run yet. */
export function makeDom(data) {
  return new JSDOM(pageHtml(data), { url: PAGE_URL });
}

/** A Storage stand-in over a plain Map, recording every write. */
export function memoryStorage(initial = {}) {
  const items = new Map(Object.entries(initial));
  return {
    items,
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => {
      items.set(key, String(value));
    },
    removeItem: (key) => {
      items.delete(key);
    },
  };
}

/** A Storage stand-in whose every access throws, as a blocked one does. */
export function throwingStorage() {
  const fail = () => {
    throw new Error("storage is blocked");
  };
  return { getItem: fail, setItem: fail, removeItem: fail };
}

/**
 * A window stand-in carrying `storage`, node's Blob (whose text() the tests
 * read back), and a URL whose object-URL calls are recorded instead of made.
 * Timers run at once.
 */
export function fakeWindow(storage) {
  const calls = { created: [], revoked: [] };
  const win = {
    localStorage: storage,
    Blob,
    URL: {
      createObjectURL(blob) {
        calls.created.push(blob);
        return "blob:retrolog-test";
      },
      revokeObjectURL(url) {
        calls.revoked.push(url);
      },
    },
    setTimeout(callback) {
      callback();
    },
  };
  return { win, calls };
}

/** Render `data` through `init` with `storage`; return the pieces tests read. */
export function renderPage(review, data, storage) {
  const dom = makeDom(data);
  const { win, calls } = fakeWindow(storage);
  review.init(dom.window.document, win);
  return { dom, document: dom.window.document, win, calls };
}

/** The `index`th entry section on the page. */
export function section(document, index) {
  return document.querySelectorAll("section.entry")[index];
}

/** Check the radio of `value` in entry `index`, firing its change event. */
export function choose(document, index, value) {
  const radio = section(document, index).querySelector(
    `input[type=radio][value="${value}"]`,
  );
  radio.checked = true;
  radio.dispatchEvent(
    new document.defaultView.Event("change", { bubbles: true }),
  );
}

/** Type `text` into entry `index`'s id field, firing input then change. */
export function typeId(document, index, text) {
  const field = section(document, index).querySelector("input.typed-id");
  field.value = text;
  const { Event } = document.defaultView;
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

/** The answers stored for SHA, parsed, or undefined when none is stored. */
export function stored(storage) {
  const text = storage.items.get(`retrolog_review:${SHA}`);
  return text === undefined ? undefined : JSON.parse(text);
}
