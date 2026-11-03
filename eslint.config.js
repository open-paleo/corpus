import eslint from "@eslint/js";
import stylistic from "@stylistic/eslint-plugin";
import globals from "globals";

// The formatting rules of the open-paleo data repository and the tree extractor, so the
// corpus web app's browser code reads the same way.
const sharedStylisticRules = {
    // Allman brace style
    "@stylistic/brace-style": ["error", "allman", { allowSingleLine: false }],

    // 4-space indentation, no tabs
    "@stylistic/indent": ["error", 4],
    "@stylistic/no-tabs": "error",

    // Double quotes
    "@stylistic/quotes": ["error", "double", { avoidEscape: true }],

    // Semicolons required
    "@stylistic/semi": ["error", "always"],

    // Trailing commas
    "@stylistic/comma-dangle": ["error", "always-multiline"],

    // Spacing
    "@stylistic/comma-spacing": "error",
    "@stylistic/key-spacing": "error",
    "@stylistic/keyword-spacing": "error",
    "@stylistic/space-before-blocks": "error",
    "@stylistic/space-infix-ops": "error",
    "@stylistic/object-curly-spacing": ["error", "always"],
    "@stylistic/arrow-spacing": "error",
    "@stylistic/block-spacing": "error",
    "@stylistic/semi-spacing": "error",
    "@stylistic/space-before-function-paren": ["error", {
        anonymous: "always",
        named: "never",
        asyncArrow: "always",
    }],

    // No trailing spaces, require final newline
    "@stylistic/no-trailing-spaces": "error",
    "@stylistic/eol-last": ["error", "always"],

    // No multiple empty lines
    "@stylistic/no-multiple-empty-lines": ["error", { max: 1, maxEOF: 0, maxBOF: 0 }],

    // Blank line after block-like statements (if, for, while, etc.)
    "@stylistic/padding-line-between-statements": [
        "error",
        { blankLine: "always", prev: "block-like", next: "*" },
    ],

    // Braces on every if/else/for/while body, even a one-line one
    curly: ["error", "all"],

    // Prefer const
    "prefer-const": "error",
    "no-var": "error",
};

export default [
    eslint.configs.recommended,
    {
        files: ["src/corpus/web/static/*.js"],
        languageOptions: {
            sourceType: "module",
            globals: globals.browser,
        },
        plugins: {
            "@stylistic": stylistic,
        },
        rules: {
            ...sharedStylisticRules,

            // camelCase names, and PascalCase for classes, as the TypeScript
            // naming-convention rule enforces; core camelcase would let
            // SCREAMING_SNAKE_CASE through. Property names are free, since they
            // mirror the server's JSON.
            "id-match": ["error", "^(?:[a-z][a-zA-Z0-9]*|(?:[A-Z][a-z0-9]+)+)$", {
                properties: false,
                onlyDeclarations: true,
                ignoreDestructuring: false,
            }],
        },
    },
    {
        // PDF.js is third-party code, vendored as released
        ignores: ["src/corpus/web/static/pdfjs/**", "node_modules/**"],
    },
];
