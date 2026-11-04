import js from "@eslint/js";
import a11y from "eslint-plugin-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const typescriptFiles = ["**/*.{ts,tsx}"];
const jsxFiles = ["**/*.tsx"];
const accessibilityRules = [
    "a11y/anchor-is-valid",
    "a11y/aria-activedescendant-has-tabindex",
    "a11y/audio-captions",
    "a11y/dialog-modal",
    "a11y/fieldset-legend",
    "a11y/heading-has-content",
    "a11y/heading-order",
    "a11y/html-has-lang",
    "a11y/iframe-title",
    "a11y/image-alt",
    "a11y/landmark-roles",
    "a11y/mouse-events-have-key-events",
    "a11y/no-access-key",
    "a11y/no-aria-hidden-on-focusable",
    "a11y/no-autofocus",
    "a11y/no-distracting-elements",
    "a11y/no-interactive-element-to-noninteractive-role",
    "a11y/no-noninteractive-element-to-interactive-role",
    "a11y/no-noninteractive-tabindex",
    "a11y/no-redundant-roles",
    "a11y/no-role-presentation-on-focusable",
    "a11y/no-static-element-interactions",
    "a11y/scope",
    "a11y/tabindex-no-positive",
    "a11y/video-captions",
];

export default tseslint.config(
    {
        ignores: [
            "**/dist/**",
            "**/build/**",
            "**/node_modules/**",
            "**/.tox/**",
            "**/.venv/**",
            "**/vendor/**",
        ],
    },
    {
        files: typescriptFiles,
        extends: [js.configs.recommended, ...tseslint.configs.recommended],
        rules: {
            "@typescript-eslint/no-unused-vars": [
                "error",
                { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
            ],
        },
    },
    {
        files: jsxFiles,
        plugins: {
            "react-hooks": reactHooks,
            a11y,
        },
        rules: {
            "react-hooks/exhaustive-deps": "error",
            "react-hooks/rules-of-hooks": "error",
            ...Object.fromEntries(
                accessibilityRules.map((rule) => [rule, "error"]),
            ),
            "a11y/image-alt": [
                "error",
                {
                    allowMissingAltOnDecorative: true,
                    decorativeMatcher: { requireAriaHidden: true },
                },
            ],
        },
    },
    {
        files: [
            "frontend/packages/**/*.tsx",
            "frontend/src/**/*.tsx",
            "containers/rhdh/plugin/**/*.tsx",
        ],
        rules: {
            "a11y/click-events-have-key-events": "error",
        },
    },
    {
        files: ["**/*.test.{ts,tsx}", "**/*.spec.{ts,tsx}"],
        rules: {
            "@typescript-eslint/no-explicit-any": "off",
            "a11y/dialog-modal": "off",
        },
    },
);
