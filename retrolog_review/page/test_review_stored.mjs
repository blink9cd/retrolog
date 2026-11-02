// Stored answers the page cannot show: an answer kept in localStorage is
// checked against its entry's own choices on load, and one that does not fit
// (an edited store, or one written for another page) is dropped rather than
// stopping every later entry from rendering.
import assert from "node:assert/strict";
import test from "node:test";

import {
  SHA,
  loadReview,
  memoryStorage,
  renderPage,
  sampleData,
  section,
  stored,
} from "./review_test_helpers.mjs";

const review = loadReview();

const FILM_TWO = "film\u0000Placeholder Film Two\u00002010/seen.txt:4";
const BOOK_ONE = "book\u0000Placeholder Book One\u00002012/read.txt:1";
const FILM_THREE = "film\u0000Placeholder Film Three\u00002013/seen.txt:2";
const BOOK_TWO = "book\u0000Placeholder Book Two\u00002014/read.txt:3";

/** A storage already holding `answers` for the sample review. */
function storageWith(answers) {
  return memoryStorage({ [`retrolog_review:${SHA}`]: JSON.stringify(answers) });
}

/** The checked radio's value in entry `index`, or null. */
function checkedValue(document, index) {
  const radio = section(document, index).querySelector(
    "input[type=radio]:checked",
  );
  return radio === null ? null : radio.value;
}

/** Each entry's checked radio, in page order. */
function checkedValues(document) {
  return [0, 1, 2, 3, 4].map((index) => checkedValue(document, index));
}

test("a stored keep for an entry with no Keep choice is dropped", () => {
  const storage = storageWith({ [FILM_TWO]: "keep", [BOOK_ONE]: "keep" });
  const { document } = renderPage(review, sampleData(), storage);
  assert.equal(document.querySelectorAll("section.entry").length, 5);
  assert.deepEqual(checkedValues(document), [
    null,
    null,
    "keep",
    "keep",
    "keep",
  ]);
  const progress = document.getElementById("progress").textContent;
  assert.equal(progress, "3 of 5 decided");
});

test("a stored id of the other medium is dropped", () => {
  const storage = storageWith({
    [FILM_TWO]: { ol_work: "/works/OL100W" },
    [BOOK_ONE]: { tmdb_id: "1000003" },
    [FILM_THREE]: { isbn13: "9780306406157" },
  });
  const { document } = renderPage(review, sampleData(), storage);
  assert.deepEqual(checkedValues(document).slice(0, 3), [null, null, "keep"]);
  assert.equal(section(document, 1).querySelector("input.typed-id").value, "");
});

test("a stored id the field would refuse is dropped", () => {
  const storage = storageWith({
    [BOOK_ONE]: { isbn13: "9780306406158" },
    [FILM_TWO]: { tmdb_id: "12a" },
  });
  const { document } = renderPage(review, sampleData(), storage);
  assert.deepEqual(checkedValues(document).slice(0, 2), [null, null]);
});

test("a stored work and ISBN pair no candidate gives is dropped", () => {
  const storage = storageWith({
    [BOOK_TWO]: { ol_work: "/works/OL999W", isbn13: "9780306406157" },
  });
  const { document } = renderPage(review, sampleData(), storage);
  assert.equal(checkedValue(document, 3), "keep");
});

test("stored answers that fit are restored, and only they are stored again", () => {
  const storage = storageWith({
    [FILM_TWO]: { tmdb_id: "1000004" },
    [BOOK_ONE]: { isbn13: "9780306406157" },
    [FILM_THREE]: "keep",
    [BOOK_TWO]: { ol_work: "/works/OL100W", isbn13: "9780000000002" },
    "book\u0000Placeholder Book Three\u00002015/read.txt:5": "keep-me-not",
  });
  const { document } = renderPage(review, sampleData(), storage);
  assert.deepEqual(checkedValues(document), [
    "candidate-1",
    "typed",
    "keep",
    "candidate-0",
    "keep",
  ]);
  const field = section(document, 1).querySelector("input.typed-id");
  assert.equal(field.value, "9780306406157");
});

test("a dropped answer leaves storage on the next pick", () => {
  const storage = storageWith({ [FILM_TWO]: "keep" });
  const { document } = renderPage(review, sampleData(), storage);
  const radio = section(document, 4).querySelector('input[value="none"]');
  radio.checked = true;
  radio.dispatchEvent(
    new document.defaultView.Event("change", { bubbles: true }),
  );
  assert.equal(stored(storage)[FILM_TWO], undefined);
});
