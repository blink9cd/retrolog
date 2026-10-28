// Forgetting the stored picks: on file:// every local page shares one origin,
// so the operator can remove this review's stored picks from the browser. The
// picks still on the page are kept there, so they can be saved as a file.
import assert from "node:assert/strict";
import test from "node:test";

import {
  SHA,
  choose,
  loadReview,
  memoryStorage,
  renderPage,
  sampleData,
  stored,
  throwingStorage,
} from "./review_test_helpers.mjs";

const review = loadReview();
const OTHER_KEY = "retrolog_review:" + "b".repeat(64);

test("forgetAnswers removes only this review's key and reports failure", () => {
  const storage = memoryStorage({ [OTHER_KEY]: "{}" });
  storage.setItem(review.storageKey(SHA), "{}");
  assert.equal(review.forgetAnswers(storage, SHA), true);
  assert.deepEqual([...storage.items.keys()], [OTHER_KEY]);
  assert.equal(review.forgetAnswers(null, SHA), false);
  assert.equal(review.forgetAnswers(throwingStorage(), SHA), false);
});

test("Forget stored picks clears storage, keeps the page, and stores no more", () => {
  const storage = memoryStorage({ [OTHER_KEY]: "{}" });
  const { document } = renderPage(review, sampleData(), storage);
  choose(document, 0, "candidate-1");
  assert.notEqual(stored(storage), undefined);
  document.getElementById("forget-picks").click();
  assert.equal(stored(storage), undefined);
  assert.equal(storage.items.has(OTHER_KEY), true);
  assert.equal(document.getElementById("forgotten").hidden, false);
  assert.equal(document.getElementById("forget-failed").hidden, true);
  // The page now keeps nothing, and says so as it does for blocked storage.
  assert.equal(document.getElementById("storage-notice").hidden, false);
  choose(document, 1, "none");
  assert.equal(stored(storage), undefined);
  // The picks made before and after are still on the page, to save as a file.
  const checked = document.querySelectorAll("input[type=radio]:checked");
  assert.equal(checked.length, 5);
});

test("Forget stored picks says so when the browser refuses the removal", () => {
  const storage = memoryStorage();
  storage.removeItem = () => {
    throw new Error("storage is blocked");
  };
  const { document } = renderPage(review, sampleData(), storage);
  document.getElementById("forget-picks").click();
  assert.equal(document.getElementById("forgotten").hidden, true);
  assert.equal(document.getElementById("forget-failed").hidden, false);
});
