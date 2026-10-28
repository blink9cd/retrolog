// The review page's browser storage: where its picks are kept between visits.
//
// A CLASSIC script, like review.js, and inlined ahead of it into the same
// <script> by retrolog_review. Every call is guarded: a browser may offer no
// localStorage (a private window, blocked site data) or refuse a read or a
// write, and the page must still work, so each helper reports failure instead
// of throwing. Its helpers are exposed, frozen, as `globalThis.RetrologStorage`
// for review.js; its shape is declared in review_types.d.ts.
"use strict";

(function (root) {
  const STORAGE_PREFIX = "retrolog_review:";

  /** The localStorage key for one review's picks. @param {string} sha */
  function storageKey(sha) {
    return STORAGE_PREFIX + sha;
  }

  /**
   * The window's localStorage, or null when it is missing or refused.
   * @param {{localStorage?: Storage}} win @returns {Storage | null}
   */
  function openStorage(win) {
    try {
      return win.localStorage ?? null;
    } catch {
      return null;
    }
  }

  /**
   * A review's stored text, null when none is stored; ok is false when
   * storage is missing or refuses the read.
   * @param {Storage | null} storage @param {string} sha
   * @returns {{text: string | null, ok: boolean}}
   */
  function readStored(storage, sha) {
    // A null storage throws here too, so one catch covers missing and refused.
    try {
      const text = /** @type {Storage} */ (storage).getItem(storageKey(sha));
      return { text, ok: true };
    } catch {
      return { text: null, ok: false };
    }
  }

  /**
   * Store every decided answer; false when storage is missing or refuses.
   * @param {Storage | null} storage @param {string} sha @param {Answers} answers
   */
  function saveAnswers(storage, sha, answers) {
    const kept = Object.fromEntries([...answers].filter(([, a]) => a !== null));
    // As in readStored, a null storage throws and lands in the same catch.
    try {
      const key = storageKey(sha);
      /** @type {Storage} */ (storage).setItem(key, JSON.stringify(kept));
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Remove a review's stored answers; false when storage is missing or
   * refuses. Other reviews' keys, and every other page's, are left alone.
   * @param {Storage | null} storage @param {string} sha
   */
  function forgetAnswers(storage, sha) {
    // As in readStored, a null storage throws and lands in the same catch.
    try {
      /** @type {Storage} */ (storage).removeItem(storageKey(sha));
      return true;
    } catch {
      return false;
    }
  }

  const api = Object.freeze({
    forgetAnswers,
    openStorage,
    readStored,
    saveAnswers,
    storageKey,
  });
  Object.defineProperty(root, "RetrologStorage", { value: api });
})(globalThis);
