import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// jsx-a11y rules that still have violations in the dashboard. They warn so new
// code sees them; promote each to an error once its existing violations are fixed.
const a11yRulesWithExistingViolations = [
  "anchor-has-content",
  "click-events-have-key-events",
  "interactive-supports-focus",
  "label-has-associated-control",
  "no-autofocus",
  "no-interactive-element-to-noninteractive-role",
  "no-noninteractive-element-interactions",
  "no-noninteractive-tabindex",
  "no-redundant-roles",
  "no-static-element-interactions",
  "role-supports-aria-props",
];

export default tseslint.config(
  { ignores: ["node_modules", "dist", "build", "coverage", "src/generated"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "jsx-a11y-x": jsxA11y,
      "react-hooks": reactHooks,
    },
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/explicit-function-return-type": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          ignoreRestSiblings: true,
        },
      ],
      // A conditional hook crashes the page when the condition flips.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      ...jsxA11y.configs.recommended.rules,
      ...Object.fromEntries(
        a11yRulesWithExistingViolations.map((rule) => [`jsx-a11y-x/${rule}`, "warn"]),
      ),
    },
  }
);
