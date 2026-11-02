// The shapes the page scripts work with, for the type checker only: none of it
// is inlined into the page. The scripts are classic, so these are global
// declarations (this file has no import or export) and their JSDoc names them
// directly. They mirror the data block and picks.json; retrolog_review's Python
// half writes the first and reads the second.

/** One candidate title, as review.json lists it. */
interface Candidate {
  title: string;
  original_title: string;
  year: number | null;
  creator: string;
  /** A TMDb id for a film, an Open Library work path for a book. */
  id: string;
  isbn13: string;
  votes: number | null;
}

/** One decidable entry: a title and every row it covers. */
interface Entry {
  kind: string;
  medium: string;
  title: string;
  rows: string[];
  detail: string;
  candidates: Candidate[];
}

/** The parts of the data block the page reads, with defaults filled in. */
interface ReviewData {
  review_sha256: string;
  not_shown: { errors: number; applied: number };
  entries: Entry[];
}

/** An answer naming an id, or "None of these". */
interface IdAnswer {
  tmdb_id?: string;
  ol_work?: string;
  isbn13?: string;
  unresolvable?: boolean;
}

/** One entry's answer, as stored and as written to picks.json. */
type Answer = "keep" | IdAnswer;

/** Entry key to answer; null is "Type an id" holding no valid id yet. */
type Answers = Map<string, Answer | null>;

/** What the page uses of its window, so tests can hand in a stand-in. */
interface PageWindow {
  /** Optional: a browser with storage switched off may offer none. */
  localStorage?: Storage;
  Blob: typeof Blob;
  URL: Pick<typeof URL, "createObjectURL" | "revokeObjectURL">;
  setTimeout(callback: () => void, ms?: number): unknown;
}

/** The page's live state, built once by `init`. */
interface Page {
  doc: Document;
  win: PageWindow;
  data: ReviewData;
  storage: Storage | null;
  answers: Answers;
}

/** What review_storage.js exposes as `globalThis.RetrologStorage`. */
interface RetrologStorageApi {
  storageKey(sha: string): string;
  openStorage(win: { localStorage?: Storage }): Storage | null;
  readStored(
    storage: Storage | null,
    sha: string,
  ): { text: string | null; ok: boolean };
  saveAnswers(storage: Storage | null, sha: string, answers: Answers): boolean;
  forgetAnswers(storage: Storage | null, sha: string): boolean;
  isAnswer(value: unknown): boolean;
  isPlainObject(value: unknown): boolean;
  loadAnswers(
    storage: Storage | null,
    sha: string,
  ): { answers: Answers; ok: boolean };
}

/** Set by review_storage.js, which the page inlines ahead of review.js. */
declare var RetrologStorage: RetrologStorageApi;

/** One entry and the section that shows it. */
interface Slot {
  page: Page;
  entry: Entry;
  index: number;
  part: HTMLElement;
}
