// Making picks: each change is stored at once and restored on load, a typed id
// is checked before it is kept, the undecided filter, blocked storage, and the
// picks file the save button writes.
import assert from "node:assert/strict";
import test from "node:test";

import {
  SHA,
  choose,
  loadReview,
  memoryStorage,
  renderPage,
  sampleData,
  section,
  stored,
  throwingStorage,
  typeId,
} from "./review_test_helpers.mjs";

const review = loadReview();

/** Normalized visible text of `node`. */
function text(node) {
  return node.textContent.replace(/\s+/g, " ").trim();
}

/** The checked radio's value in entry `index`, or null. */
function checkedValue(document, index) {
  const radio = section(document, index).querySelector(
    "input[type=radio]:checked",
  );
  return radio === null ? null : radio.value;
}

test("picking a candidate stores it at once and a reload restores it", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  choose(document, 0, "candidate-1");
  assert.deepEqual(stored(storage), {
    "film\u0000Placeholder Film Two\u00002010/seen.txt:4": {
      tmdb_id: "1000004",
    },
  });
  assert.equal(text(document.getElementById("progress")), "4 of 5 decided");
  assert.equal(section(document, 0).classList.contains("decided"), true);
  const reloaded = renderPage(review, sampleData(), storage).document;
  assert.equal(checkedValue(reloaded, 0), "candidate-1");
});

test("a book candidate stores its work and ISBN; Keep and None store theirs", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  choose(document, 3, "candidate-0");
  choose(document, 4, "none");
  choose(document, 3, "keep");
  assert.deepEqual(stored(storage), {
    "book\u0000Placeholder Book Two\u00002014/read.txt:3": "keep",
    "book\u0000Placeholder Book Three\u00002015/read.txt:5": {
      unresolvable: true,
    },
  });
  choose(document, 3, "candidate-0");
  assert.deepEqual(
    stored(storage)["book\u0000Placeholder Book Two\u00002014/read.txt:3"],
    {
      ol_work: "/works/OL100W",
      isbn13: "9780000000002",
    },
  );
  const reloaded = renderPage(review, sampleData(), storage).document;
  assert.equal(checkedValue(reloaded, 3), "candidate-0");
  assert.equal(checkedValue(reloaded, 4), "none");
});

test("a valid typed id is stored and restored into its field", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  typeId(document, 1, "978-0-00-000000-2");
  assert.equal(checkedValue(document, 1), "typed");
  assert.deepEqual(
    stored(storage)["book\u0000Placeholder Book One\u00002012/read.txt:1"],
    {
      isbn13: "9780000000002",
    },
  );
  const reloaded = renderPage(review, sampleData(), storage).document;
  assert.equal(checkedValue(reloaded, 1), "typed");
  assert.equal(
    section(reloaded, 1).querySelector("input.typed-id").value,
    "9780000000002",
  );
});

test("a typed film id that matches a candidate restores as that candidate", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  typeId(document, 0, "1000003");
  const reloaded = renderPage(review, sampleData(), storage).document;
  assert.equal(checkedValue(reloaded, 0), "candidate-0");
});

test("a typed work id is restored into the field", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  typeId(document, 1, "/works/OL900W");
  const reloaded = renderPage(review, sampleData(), storage).document;
  assert.equal(
    section(reloaded, 1).querySelector("input.typed-id").value,
    "/works/OL900W",
  );
});

test("an invalid typed id shows a message, is not saved, and leaves it undecided", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  choose(document, 0, "candidate-0");
  typeId(document, 0, "12a");
  const entry = section(document, 0);
  const field = entry.querySelector("input.typed-id");
  assert.equal(field.getAttribute("aria-invalid"), "true");
  assert.equal(
    text(entry.querySelector(".field-message")),
    "Not a TMDb id: type its digits only.",
  );
  assert.equal(
    stored(storage)["film\u0000Placeholder Film Two\u00002010/seen.txt:4"],
    undefined,
  );
  assert.equal(entry.classList.contains("decided"), false);
  typeId(document, 0, "7");
  assert.equal(field.hasAttribute("aria-invalid"), false);
  assert.equal(text(entry.querySelector(".field-message")), "");
  assert.deepEqual(
    stored(storage)["film\u0000Placeholder Film Two\u00002010/seen.txt:4"],
    {
      tmdb_id: "7",
    },
  );
});

test("an invalid book id names the forms a book takes", () => {
  const { document } = renderPage(review, sampleData(), memoryStorage());
  typeId(document, 1, "OL1W");
  assert.equal(
    text(section(document, 1).querySelector(".field-message")),
    "Not a work id or ISBN: type /works/OL…W, or 10 or 13 digits.",
  );
});

test("choosing Type an id with an empty field is undecided and silent", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  choose(document, 2, "typed");
  const entry = section(document, 2);
  assert.equal(text(entry.querySelector(".field-message")), "");
  assert.equal(entry.classList.contains("decided"), false);
  assert.equal(text(document.getElementById("progress")), "2 of 5 decided");
  assert.equal(
    stored(storage)["film\u0000Placeholder Film Three\u00002013/seen.txt:2"],
    undefined,
  );
});

test("choosing Type an id uses an id already in the field", () => {
  const storage = memoryStorage();
  const { document } = renderPage(review, sampleData(), storage);
  section(document, 2).querySelector("input.typed-id").value = "55";
  choose(document, 2, "typed");
  assert.deepEqual(
    stored(storage)["film\u0000Placeholder Film Three\u00002013/seen.txt:2"],
    {
      tmdb_id: "55",
    },
  );
});

test("Show only undecided hides decided entries, now and after each pick", () => {
  const { document } = renderPage(review, sampleData(), memoryStorage());
  const toggle = document.getElementById("only-undecided");
  toggle.checked = true;
  toggle.dispatchEvent(new document.defaultView.Event("change"));
  const hidden = () =>
    [0, 1, 2, 3, 4].map((index) => section(document, index).hidden);
  assert.deepEqual(hidden(), [false, false, true, true, true]);
  choose(document, 0, "none");
  assert.deepEqual(hidden(), [true, false, true, true, true]);
  toggle.checked = false;
  toggle.dispatchEvent(new document.defaultView.Event("change"));
  assert.deepEqual(hidden(), [false, false, false, false, false]);
});

test("typing does not hide the entry being typed into until the field is left", () => {
  const { document } = renderPage(review, sampleData(), memoryStorage());
  const toggle = document.getElementById("only-undecided");
  toggle.checked = true;
  toggle.dispatchEvent(new document.defaultView.Event("change"));
  const field = section(document, 0).querySelector("input.typed-id");
  field.value = "9";
  field.dispatchEvent(new document.defaultView.Event("input"));
  assert.equal(section(document, 0).hidden, false);
  // A real change event bubbles to the entry, as a browser's does.
  field.dispatchEvent(
    new document.defaultView.Event("change", { bubbles: true }),
  );
  assert.equal(section(document, 0).hidden, true);
});

test("storage that throws on read shows the notice and the page still works", () => {
  const { document } = renderPage(review, sampleData(), throwingStorage());
  assert.equal(document.getElementById("storage-notice").hidden, false);
  choose(document, 0, "candidate-0");
  assert.equal(text(document.getElementById("progress")), "4 of 5 decided");
});

test("storage that fails on write shows the notice at the first pick", () => {
  const storage = memoryStorage();
  storage.setItem = () => {
    throw new Error("quota");
  };
  const { document } = renderPage(review, sampleData(), storage);
  const notice = document.getElementById("storage-notice");
  assert.equal(notice.hidden, true);
  choose(document, 0, "none");
  assert.equal(notice.hidden, false);
  assert.match(text(notice), /picks, so they will be lost on reload/);
});

test("Save picks file downloads the exact picks JSON, then revokes its URL", async () => {
  const storage = memoryStorage();
  const { document, calls } = renderPage(review, sampleData(), storage);
  choose(document, 0, "candidate-0");
  typeId(document, 1, "/works/OL900W");
  choose(document, 4, "none");
  const clicked = [];
  document.addEventListener("click", (event) => {
    if (event.target.tagName === "A") {
      clicked.push(event.target.download, event.target.href);
      event.preventDefault();
    }
  });
  document.getElementById("save-picks").click();
  assert.equal(calls.created.length, 1);
  assert.equal(calls.created[0].type, "application/json");
  const expected = {
    schema: 1,
    review_sha256: SHA,
    picks: [
      {
        medium: "film",
        title: "Placeholder Film Two",
        rows: ["2010/seen.txt:4", "2011/seen.txt:9"],
        answer: { tmdb_id: "1000003" },
      },
      {
        medium: "book",
        title: "Placeholder Book One",
        rows: ["2012/read.txt:1"],
        answer: { ol_work: "/works/OL900W" },
      },
      {
        medium: "film",
        title: "Placeholder Film Three",
        rows: ["2013/seen.txt:2"],
        answer: "keep",
      },
      {
        medium: "book",
        title: "Placeholder Book Two",
        rows: ["2014/read.txt:3"],
        answer: "keep",
      },
      {
        medium: "book",
        title: "Placeholder Book Three",
        rows: ["2015/read.txt:5"],
        answer: { unresolvable: true },
      },
    ],
  };
  assert.equal(
    await calls.created[0].text(),
    `${JSON.stringify(expected, null, 2)}\n`,
  );
  assert.deepEqual(clicked, ["picks.json", "blob:retrolog-test"]);
  assert.deepEqual(calls.revoked, ["blob:retrolog-test"]);
  assert.equal(document.querySelectorAll("a[download]").length, 0);
});

test("two entries sharing a title are stored, restored and saved apart", () => {
  const data = sampleData();
  // resolve groups by both years too, so one title can make a second entry,
  // told apart only by its rows.
  data.entries.splice(1, 0, {
    ...data.entries[0],
    kind: "not-found",
    rows: ["2012/seen.txt:3"],
    candidates: [],
  });
  const storage = memoryStorage();
  const { document } = renderPage(review, data, storage);
  choose(document, 1, "none");
  assert.deepEqual(stored(storage), {
    "film\u0000Placeholder Film Two\u00002012/seen.txt:3": {
      unresolvable: true,
    },
  });
  const reloaded = renderPage(review, data, storage).document;
  assert.equal(checkedValue(reloaded, 0), null);
  assert.equal(checkedValue(reloaded, 1), "none");
  choose(reloaded, 0, "candidate-1");
  const answers = new Map(Object.entries(stored(storage)));
  assert.deepEqual(review.buildPicks(data, answers).picks.slice(0, 2), [
    {
      medium: "film",
      title: "Placeholder Film Two",
      rows: ["2010/seen.txt:4", "2011/seen.txt:9"],
      answer: { tmdb_id: "1000004" },
    },
    {
      medium: "film",
      title: "Placeholder Film Two",
      rows: ["2012/seen.txt:3"],
      answer: { unresolvable: true },
    },
  ]);
});
