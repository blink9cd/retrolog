// The template's contract with the page writer, and the page as a browser runs
// it. retrolog_review replaces three whole marker lines and the data block's
// `{}`, refusing a template where any is missing or repeated, so each is pinned
// here byte for byte. Then the script is inlined the way the writer inlines it
// and run by jsdom itself, which proves it works as a classic script whose own
// boot finds the page.
import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  SCRIPT_SOURCE,
  TEMPLATE,
  pageHtml,
  sampleData,
} from "./review_test_helpers.mjs";

const MARKERS = [
  '<link rel="stylesheet" href="review.css">',
  '<script src="review.js"></script>',
  '<script type="application/json" id="review-data">{}</script>',
];

/** How many lines of the template are exactly `line`. */
function linesEqual(line) {
  return TEMPLATE.split("\n").filter((each) => each === line).length;
}

test("each marker is one whole line, exactly once", () => {
  for (const marker of MARKERS) {
    assert.equal(linesEqual(marker), 1, marker);
  }
  const policy = TEMPLATE.split("\n").filter((line) =>
    line.startsWith('<meta http-equiv="Content-Security-Policy" content="'),
  );
  assert.equal(policy.length, 1);
  assert.ok(policy[0].endsWith('">'));
});

test("the placeholder policy blocks every request the page could make", () => {
  const policy = TEMPLATE.match(
    /Content-Security-Policy" content="([^"]*)"/,
  )[1];
  for (const directive of [
    "connect-src",
    "img-src",
    "font-src",
    "form-action",
  ]) {
    assert.match(policy, new RegExp(`${directive} 'none'`));
  }
  assert.match(TEMPLATE, /<meta name="referrer" content="no-referrer">/);
});

test("the head carries the language, viewport and title", () => {
  const { document } = new JSDOM(TEMPLATE).window;
  assert.equal(document.documentElement.lang, "en");
  assert.equal(document.title, "Retrolog review");
  const viewport = document.querySelector('meta[name="viewport"]');
  assert.equal(viewport.content, "width=device-width, initial-scale=1");
});

test("the script makes no request and uses no module syntax", () => {
  const banned = [
    /\bfetch\s*\(/,
    /XMLHttpRequest/,
    /^\s*(?:import|export)\b/m,
    /\bimport\s*\(/,
    /new Image\b/,
    /sendBeacon/,
    /WebSocket/,
    /EventSource/,
  ];
  for (const pattern of banned) {
    assert.doesNotMatch(SCRIPT_SOURCE, pattern);
  }
});

test("inlined as a classic script, the page renders itself on load", () => {
  const html = pageHtml(sampleData()).replace(
    '<script src="review.js"></script>',
    `<script>${SCRIPT_SOURCE}</script>`,
  );
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    url: "http://localhost/review.html",
  });
  const { document } = dom.window;
  assert.equal(document.querySelectorAll("section.entry").length, 5);
  assert.equal(
    document.getElementById("progress").textContent,
    "3 of 5 decided",
  );
  assert.equal(typeof dom.window.RetrologReview.init, "function");
  dom.window.close();
});
