// What the page shows on load: the header, one section per entry, each
// candidate's facts and catalog link, and which choice each kind starts on.
import assert from "node:assert/strict";
import test from "node:test";

import {
  candidate,
  choose,
  fakeWindow,
  loadReview,
  makeDom,
  memoryStorage,
  renderPage,
  sampleData,
  section,
  stored,
} from "./review_test_helpers.mjs";

const review = loadReview();

/** Render the sample with empty storage. */
function sample() {
  return renderPage(review, sampleData(), memoryStorage()).document;
}

/** Normalized visible text of `node`. */
function text(node) {
  return node.textContent.replace(/\s+/g, " ").trim();
}

test("the header shows progress, the not-shown line and the controls", () => {
  const document = sample();
  assert.equal(text(document.getElementById("progress")), "3 of 5 decided");
  const notShown = document.getElementById("not-shown");
  assert.equal(notShown.hidden, false);
  assert.equal(
    text(notShown),
    "Not shown here: 1 error and 4 applied overrides. Fix the errors by editing the overrides file by hand, as review.md describes; applied overrides need nothing.",
  );
  assert.equal(document.getElementById("storage-notice").hidden, true);
  assert.equal(text(document.getElementById("save-picks")), "Save picks file");
});

test("the not-shown line names whichever count is nonzero", () => {
  const data = sampleData();
  data.not_shown = { errors: 2, applied: 0 };
  const { document } = renderPage(review, data, memoryStorage());
  assert.match(
    text(document.getElementById("not-shown")),
    /^Not shown here: 2 errors and 0 applied overrides\./,
  );
  data.not_shown = { errors: 0, applied: 1 };
  const again = renderPage(review, data, memoryStorage()).document;
  assert.match(
    text(again.getElementById("not-shown")),
    /0 errors and 1 applied override\./,
  );
});

test("the not-shown line stays hidden when both counts are zero", () => {
  const data = sampleData();
  data.not_shown = { errors: 0, applied: 0 };
  const { document } = renderPage(review, data, memoryStorage());
  assert.equal(document.getElementById("not-shown").hidden, true);
});

test("each entry shows its title, kind, medium, rows and detail", () => {
  const document = sample();
  assert.equal(document.querySelectorAll("section.entry").length, 5);
  const first = section(document, 0);
  assert.equal(text(first.querySelector("h2")), "Placeholder Film Two");
  assert.equal(
    text(first.querySelector(".entry-meta")),
    "Kind: ambiguous, medium: film",
  );
  assert.equal(
    text(first.querySelector(".entry-rows")),
    "Rows: 2010/seen.txt:4, 2011/seen.txt:9",
  );
  assert.equal(text(first.querySelector(".entry-detail")), "2 exact titles");
  assert.equal(section(document, 1).querySelector(".entry-detail"), null);
});

test("a candidate shows its facts, and the first is marked Suggested", () => {
  const first = section(sample(), 0);
  const labels = first.querySelectorAll(".candidate label");
  const part = (label, name) => text(label.querySelector(`.${name}`));
  assert.equal(part(labels[0], "cand-title"), "Placeholder Film Two");
  assert.equal(part(labels[0], "suggested"), "Suggested");
  assert.equal(
    part(labels[0], "cand-original"),
    "Original title: Invented Original Two",
  );
  assert.equal(
    part(labels[0], "cand-facts"),
    "1927, Invented Director, 120 votes",
  );
  assert.equal(text(labels[1]), "Placeholder Film Two");
  assert.equal(first.querySelectorAll(".suggested").length, 1);
  const third = section(sample(), 2).querySelector(".candidate label");
  assert.match(text(third), /, 1 vote$/);
  assert.equal(third.querySelector(".cand-original"), null);
});

test("catalog links open only on click, in a new tab, with no referrer", () => {
  const document = sample();
  const film = section(document, 0).querySelector("a.catalog-link");
  assert.equal(film.href, "https://www.themoviedb.org/movie/1000003");
  assert.equal(film.target, "_blank");
  assert.equal(film.rel, "noopener noreferrer");
  assert.equal(text(film), "View on TMDb");
  const book = section(document, 3).querySelector("a.catalog-link");
  assert.equal(book.href, "https://openlibrary.org/works/OL100W");
  assert.equal(text(book), "View on Open Library");
});

test("a candidate with no usable id gets no link", () => {
  const data = sampleData();
  data.entries[3].candidates[0].id = "";
  const { document } = renderPage(review, data, memoryStorage());
  assert.equal(section(document, 3).querySelector("a.catalog-link"), null);
});

test("the page loads nothing: no image, frame, font or extra script", () => {
  const document = sample();
  const loaders = document.querySelectorAll(
    "img, iframe, object, embed, video, audio",
  );
  assert.equal(loaders.length, 0);
  for (const anchor of document.querySelectorAll("a")) {
    assert.ok(anchor.classList.contains("catalog-link"), anchor.href);
  }
});

test("choices: candidates, a typed id, none, and Keep only for keep kinds", () => {
  const document = sample();
  const values = (index) =>
    [...section(document, index).querySelectorAll("input[type=radio]")].map(
      (radio) => radio.value,
    );
  assert.deepEqual(values(0), ["candidate-0", "candidate-1", "typed", "none"]);
  assert.deepEqual(values(1), ["typed", "none"]);
  assert.deepEqual(values(2), ["keep", "candidate-0", "typed", "none"]);
});

test("keep kinds start on Keep; ambiguous and not-found start undecided", () => {
  const document = sample();
  const checked = [0, 1, 2, 3, 4].map((index) => {
    const radio = section(document, index).querySelector(
      "input[type=radio]:checked",
    );
    return radio === null ? null : radio.value;
  });
  assert.deepEqual(checked, [null, null, "keep", "keep", "keep"]);
  assert.equal(section(document, 0).classList.contains("decided"), false);
  assert.equal(section(document, 2).classList.contains("decided"), true);
});

test("a re-searched entry is shown, offers Keep and starts on it", () => {
  const data = sampleData();
  data.entries = [
    {
      kind: "re-searched",
      medium: "film",
      title: "The Placeholder Hour",
      rows: ["2010/seen.txt:15"],
      detail: "found by searching without a leading article",
      candidates: [candidate({ title: "Placeholder Hour", id: "1000015" })],
    },
  ];
  const { document } = renderPage(review, data, memoryStorage());
  const only = section(document, 0);
  assert.equal(text(only.querySelector("h2")), "The Placeholder Hour");
  assert.equal(
    text(only.querySelector(".entry-meta")),
    "Kind: re-searched, medium: film",
  );
  const radios = [...only.querySelectorAll("input[type=radio]")];
  assert.deepEqual(
    radios.map((radio) => radio.value),
    ["keep", "candidate-0", "typed", "none"],
  );
  assert.equal(only.querySelector("input[type=radio]:checked").value, "keep");
  assert.equal(only.classList.contains("decided"), true);
});

test("a suspect-exact entry starts undecided, and its one candidate can be picked", () => {
  const data = sampleData();
  data.entries = [
    {
      kind: "suspect-exact",
      medium: "film",
      title: "Placeholder Film Seven",
      rows: ["2010/seen.txt:16"],
      detail: "one exact title, held: no release date",
      candidates: [
        candidate({ title: "Placeholder Film Seven", id: "1000016" }),
      ],
    },
  ];
  const storage = memoryStorage();
  const { document } = renderPage(review, data, storage);
  const only = section(document, 0);
  assert.equal(
    text(only.querySelector(".entry-meta")),
    "Kind: suspect-exact, medium: film",
  );
  const radios = [...only.querySelectorAll("input[type=radio]")];
  assert.deepEqual(
    radios.map((radio) => radio.value),
    ["candidate-0", "typed", "none"],
  );
  assert.equal(only.querySelector("input[type=radio]:checked"), null);
  assert.equal(only.classList.contains("decided"), false);
  choose(document, 0, "candidate-0");
  assert.deepEqual(stored(storage), {
    "film\u0000Placeholder Film Seven\u00002010/seen.txt:16": {
      tmdb_id: "1000016",
    },
  });
  assert.equal(only.classList.contains("decided"), true);
});

test("every input has a label, and each entry's choices share a fieldset", () => {
  const document = sample();
  for (const input of document.querySelectorAll("input")) {
    assert.ok(input.labels.length > 0, input.outerHTML);
  }
  const legend = section(document, 0).querySelector("fieldset > legend");
  assert.equal(text(legend), "Pick for Placeholder Film Two");
});

test("the id field's label says what a film or a book takes", () => {
  const document = sample();
  const label = (index) =>
    text(section(document, index).querySelector("input.typed-id").labels[0]);
  assert.equal(label(0), "TMDb id");
  assert.equal(label(1), "Open Library work id or ISBN");
});

test("a page with no entries says there is nothing to decide", () => {
  const { document } = renderPage(review, {}, memoryStorage());
  assert.equal(text(document.getElementById("progress")), "0 of 0 decided");
  const empty = document.getElementById("empty");
  assert.equal(empty.hidden, false);
  assert.equal(text(empty), "Nothing to decide.");
  assert.equal(document.getElementById("unreadable").hidden, true);
});

test("a page with entries does not say there is nothing to decide", () => {
  assert.equal(sample().getElementById("empty").hidden, true);
});

test("a page whose data cannot be read says so and renders nothing else", () => {
  const { document } = renderPage(review, [], memoryStorage());
  const unreadable = document.getElementById("unreadable");
  assert.equal(unreadable.hidden, false);
  assert.equal(unreadable.getAttribute("role"), "alert");
  assert.equal(
    text(unreadable),
    "The review data on this page could not be read.",
  );
  assert.equal(text(document.getElementById("entries")), "");
  assert.equal(text(document.getElementById("progress")), "");
});

test("a page whose data names another schema says it cannot be read", () => {
  const data = { ...sampleData(), schema: 2 };
  const { document } = renderPage(review, data, memoryStorage());
  assert.equal(document.getElementById("unreadable").hidden, false);
  assert.equal(document.querySelectorAll("section.entry").length, 0);
});

test("boot renders a page whose window carries the document", () => {
  const dom = makeDom(sampleData());
  const { win } = fakeWindow(memoryStorage());
  assert.equal(review.boot({ ...win, document: dom.window.document }), true);
  const progress = dom.window.document.getElementById("progress");
  assert.equal(text(progress), "3 of 5 decided");
});
