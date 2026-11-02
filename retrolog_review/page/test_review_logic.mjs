// review.js's pure helpers: typed-id classification, answers, picks, the
// storage key and value, and reading the data block. The page suites exercise
// the same functions through the DOM; these pin each rule on its own.
import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  SHA,
  candidate,
  loadReview,
  memoryStorage,
  pageHtml,
  sampleData,
  throwingStorage,
} from "./review_test_helpers.mjs";

const review = loadReview();

test("the namespace is frozen and holds the documented functions", () => {
  assert.ok(Object.isFrozen(review));
  for (const name of [
    "boot",
    "buildPicks",
    "candidateAnswer",
    "catalogUrl",
    "classifyTypedId",
    "countOf",
    "entryKey",
    "forgetAnswers",
    "init",
    "isAnswer",
    "loadAnswers",
    "openStorage",
    "readData",
    "saveAnswers",
    "startingAnswer",
    "storageKey",
  ]) {
    assert.equal(typeof review[name], "function", name);
  }
});

test("the storage key carries the review's digest", () => {
  assert.equal(review.storageKey(SHA), `retrolog_review:${SHA}`);
});

test("an entry's key is its medium, title and first row joined by NUL", () => {
  const key = review.entryKey({
    medium: "film",
    title: "Placeholder Film Two",
    rows: ["2010/seen.txt:4", "2011/seen.txt:9"],
  });
  assert.equal(key, "film\u0000Placeholder Film Two\u00002010/seen.txt:4");
});

test("one title in two entries has two keys", () => {
  const entry = { medium: "film", title: "Placeholder Film Two" };
  assert.notEqual(
    review.entryKey({ ...entry, rows: ["2010/seen.txt:4"] }),
    review.entryKey({ ...entry, rows: ["2012/seen.txt:3"] }),
  );
});

test("a film's typed id is a TMDb id, surrounding space trimmed", () => {
  assert.deepEqual(review.classifyTypedId("film", "1000003"), {
    tmdb_id: "1000003",
  });
  assert.deepEqual(review.classifyTypedId("film", " 42 "), { tmdb_id: "42" });
  for (const bad of [
    "",
    "12a",
    "/works/OL1W",
    "-5",
    "1 000",
    "0603",
    "12345678901",
  ]) {
    assert.equal(review.classifyTypedId("film", bad), null, bad);
  }
});

test("a book's typed work id must be /works/OL<digits>W", () => {
  assert.deepEqual(review.classifyTypedId("book", "/works/OL123W"), {
    ol_work: "/works/OL123W",
  });
  for (const bad of ["OL123W", "/works/OL123M", "/works/OLW", "/books/OL1W"]) {
    assert.equal(review.classifyTypedId("book", bad), null, bad);
  }
});

test("a book's typed ISBN drops hyphens and spaces and allows a final X", () => {
  const cases = [
    ["978-0-00-000000-2", "9780000000002"],
    ["978-0-306-40615-7", "9780306406157"],
    ["9790000000001", "9790000000001"],
    ["0-306-40615-2", "0306406152"],
    ["0 00 000006 X", "000000006X"],
    ["000000006x", "000000006X"],
    ["0000000000", "0000000000"],
  ];
  for (const [typed, isbn] of cases) {
    assert.deepEqual(review.classifyTypedId("book", typed), { isbn13: isbn });
  }
  for (const bad of [
    "",
    "12345",
    "00000000000",
    "X000000000",
    "97800000000021",
  ]) {
    assert.equal(review.classifyTypedId("book", bad), null, bad);
  }
});

test("a book's typed ISBN needs the check digit retrolog_review checks", () => {
  // The same rule as resolve's isbn13_or_none, which --mode overrides applies
  // to the saved picks: a typo the page let through would refuse the file.
  for (const bad of [
    "9780306406158",
    "978030640615X",
    "000000000X",
    "0306406153",
    "0000000000000",
    "9770000000002",
  ]) {
    assert.equal(review.classifyTypedId("book", bad), null, bad);
  }
});

test("a film candidate answers with its TMDb id", () => {
  assert.deepEqual(review.candidateAnswer("film", candidate()), {
    tmdb_id: "1000003",
  });
});

test("a book candidate answers with its work, and its ISBN only if it has one", () => {
  const work = candidate({ id: "/works/OL100W" });
  assert.deepEqual(review.candidateAnswer("book", work), {
    ol_work: "/works/OL100W",
  });
  const withIsbn = { ...work, isbn13: "9780000000002" };
  assert.deepEqual(review.candidateAnswer("book", withIsbn), {
    ol_work: "/works/OL100W",
    isbn13: "9780000000002",
  });
});

test("catalog links go to TMDb for a film and Open Library for a book", () => {
  assert.equal(
    review.catalogUrl("film", "1000003"),
    "https://www.themoviedb.org/movie/1000003",
  );
  assert.equal(
    review.catalogUrl("book", "/works/OL100W"),
    "https://openlibrary.org/works/OL100W",
  );
});

test("an id not in its catalog's shape builds no link, so it cannot change host", () => {
  const films = ["", "1/2", "1000003x", "/works/OL100W", "\u0661", "0603"];
  for (const id of films) assert.equal(review.catalogUrl("film", id), null, id);
  const books = [
    "",
    "@elsewhere.invalid",
    "//elsewhere.invalid/x",
    "/works/OL100W/../../x",
    "/a",
    "1000003",
  ];
  for (const id of books) assert.equal(review.catalogUrl("book", id), null, id);
});

test("most-voted, variant and no-isbn start on keep; the rest undecided", () => {
  const starts = sampleData().entries.map((e) => review.startingAnswer(e));
  assert.deepEqual(starts, [null, null, "keep", "keep", "keep"]);
});

test("re-searched starts on keep, as variant does", () => {
  const entry = { ...sampleData().entries[3], kind: "re-searched" };
  assert.equal(review.startingAnswer(entry), "keep");
});

test("isAnswer accepts exactly the answer shapes a pick may hold", () => {
  const good = [
    "keep",
    { unresolvable: true },
    { tmdb_id: "1" },
    { ol_work: "/works/OL1W", isbn13: "9780000000002" },
    { isbn13: "0000000000" },
  ];
  for (const value of good) {
    assert.ok(review.isAnswer(value), JSON.stringify(value));
  }
  const bad = [
    "skip",
    null,
    7,
    [],
    {},
    { unresolvable: false },
    { unresolvable: true, tmdb_id: "1" },
    { tmdb_id: "1", ol_work: "/works/OL1W" },
    { tmdb_id: "1", isbn13: "0000000000" },
    { tmdb_id: "1", ol_work: "/works/OL1W", isbn13: "0000000000" },
    { tmdb_id: 1 },
    { tmdb_id: "" },
    { title: "x" },
  ];
  for (const value of bad) {
    assert.equal(review.isAnswer(value), false, JSON.stringify(value));
  }
});

test("picks follow entry order, include keep, and omit the undecided", () => {
  const data = sampleData();
  const answers = new Map([
    [review.entryKey(data.entries[2]), { tmdb_id: "1000003" }],
    [review.entryKey(data.entries[0]), { unresolvable: true }],
    [review.entryKey(data.entries[3]), null],
  ]);
  assert.deepEqual(review.buildPicks(data, answers), {
    schema: 1,
    review_sha256: SHA,
    picks: [
      {
        medium: "film",
        title: "Placeholder Film Two",
        rows: ["2010/seen.txt:4", "2011/seen.txt:9"],
        answer: { unresolvable: true },
      },
      {
        medium: "film",
        title: "Placeholder Film Three",
        rows: ["2013/seen.txt:2"],
        answer: { tmdb_id: "1000003" },
      },
      {
        medium: "book",
        title: "Placeholder Book Three",
        rows: ["2015/read.txt:5"],
        answer: "keep",
      },
    ],
  });
});

test("counts are pluralized", () => {
  assert.equal(review.countOf(1, "vote"), "1 vote");
  assert.equal(review.countOf(0, "error"), "0 errors");
  assert.equal(review.countOf(4, "error"), "4 errors");
});

test("openStorage returns null when storage throws or is missing", () => {
  const blocked = {
    get localStorage() {
      throw new Error("blocked");
    },
  };
  assert.equal(review.openStorage(blocked), null);
  assert.equal(review.openStorage({}), null);
  const storage = memoryStorage();
  assert.equal(review.openStorage({ localStorage: storage }), storage);
});

test("loadAnswers reports storage that is absent or throws", () => {
  assert.equal(review.loadAnswers(null, SHA).ok, false);
  const thrown = review.loadAnswers(throwingStorage(), SHA);
  assert.equal(thrown.ok, false);
  assert.equal(thrown.answers.size, 0);
});

test("loadAnswers reads valid answers and drops the rest", () => {
  const value = JSON.stringify({
    "film\u0000Placeholder Film Two\u00002010/seen.txt:4": {
      tmdb_id: "1000003",
    },
    "film\u0000Placeholder Film Three\u00002013/seen.txt:2": "keep",
    "book\u0000Placeholder Book One\u00002012/read.txt:1": {
      title: "not an answer",
    },
  });
  const loaded = review.loadAnswers(
    memoryStorage({ [`retrolog_review:${SHA}`]: value }),
    SHA,
  );
  assert.equal(loaded.ok, true);
  assert.deepEqual(
    [...loaded.answers],
    [
      [
        "film\u0000Placeholder Film Two\u00002010/seen.txt:4",
        { tmdb_id: "1000003" },
      ],
      ["film\u0000Placeholder Film Three\u00002013/seen.txt:2", "keep"],
    ],
  );
});

test("loadAnswers treats a missing, corrupt or non-object value as empty", () => {
  for (const value of [undefined, "{not json", "[1]", '"keep"', "null"]) {
    const initial =
      value === undefined ? {} : { [`retrolog_review:${SHA}`]: value };
    const loaded = review.loadAnswers(memoryStorage(initial), SHA);
    assert.equal(loaded.ok, true, String(value));
    assert.equal(loaded.answers.size, 0, String(value));
  }
});

test("saveAnswers writes every decided answer and reports failure", () => {
  const storage = memoryStorage();
  const answers = new Map([
    ["film\u0000A", { tmdb_id: "1" }],
    ["film\u0000B", null],
  ]);
  assert.equal(review.saveAnswers(storage, SHA, answers), true);
  assert.deepEqual(JSON.parse(storage.getItem(`retrolog_review:${SHA}`)), {
    "film\u0000A": { tmdb_id: "1" },
  });
  assert.equal(review.saveAnswers(null, SHA, answers), false);
  assert.equal(review.saveAnswers(throwingStorage(), SHA, answers), false);
});

/** A bare document whose body is `body`. */
function documentWith(body) {
  return new JSDOM(`<!doctype html><body>${body}</body>`).window.document;
}

test("readData reads the embedded block, escaped < included", () => {
  const data = sampleData();
  data.entries[0].title = "Placeholder <Film> Two";
  const { document } = new JSDOM(pageHtml(data)).window;
  assert.deepEqual(review.readData(document), {
    review_sha256: SHA,
    not_shown: { errors: 1, applied: 4 },
    entries: data.entries,
  });
});

test("readData gives the bare template's {} an empty review", () => {
  const { document } = new JSDOM(pageHtml({})).window;
  assert.deepEqual(review.readData(document), {
    review_sha256: "",
    not_shown: { errors: 0, applied: 0 },
    entries: [],
  });
});

test("readData returns null for a missing, corrupt or non-object block", () => {
  assert.equal(review.readData(documentWith("")), null);
  const block = (text) =>
    documentWith(
      `<script type="application/json" id="review-data">${text}</script>`,
    );
  for (const text of ["{oops", "[]", "null", "3"]) {
    assert.equal(review.readData(block(text)), null, text);
  }
});

test("readData returns null for a schema other than the one Python writes", () => {
  const { schema, ...unversioned } = sampleData();
  assert.equal(schema, 1);
  for (const data of [
    { ...unversioned, schema: 2 },
    { ...unversioned, schema: "1" },
    { ...unversioned, schema: true },
    unversioned,
  ]) {
    const { document } = new JSDOM(pageHtml(data)).window;
    assert.equal(review.readData(document), null, JSON.stringify(data.schema));
  }
});

test("boot does nothing where there is no document", () => {
  assert.equal(review.boot({}), false);
});
