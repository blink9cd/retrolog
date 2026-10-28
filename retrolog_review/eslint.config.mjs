// ESLint flat config. `.mjs` so it is parsed as ESM whatever package.json says.
// The presets resolve from THIS project's node_modules; npm's global prefix is
// never on that walk.
import js from "@eslint/js";
import globals from "globals";

// The project's additions to the recommended preset, for both blocks below.
const RULES = {
  eqeqeq: "error",
  "prefer-const": "error",
  "no-var": "error",
};

export default [
  js.configs.recommended,
  {
    // The page script is a CLASSIC script: retrolog_review inlines it into a
    // plain <script> so the page runs from file://, so `import` and `export`
    // must be parse errors here rather than pass as a module would let them.
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: globals.browser,
    },
    rules: RULES,
  },
  {
    // Every `.mjs` here runs under Node: the suites, their helpers and this
    // file, so the block keys on the extension rather than on file names.
    files: ["**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.node,
    },
    rules: RULES,
  },
];
