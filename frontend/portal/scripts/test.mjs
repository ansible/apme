/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { prepareSdk } from "./sdk.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const portal = prepareSdk();
const staging = mkdtempSync(join(tmpdir(), "apme-portal-jest-"));
const frontend = process.argv.includes("--frontend");
const hostRequire = createRequire(join(portal, "package.json"));
try {
    const config = {
        rootDir: join(root, "frontend/portal"),
        testEnvironment: frontend ? "jsdom" : "node",
        testMatch: frontend
            ? ["<rootDir>/src/frontend/**/*.test.[jt]s?(x)"]
            : [
                  "<rootDir>/src/catalog/**/*.test.ts",
                  "<rootDir>/src/scaffolder/**/*.test.ts",
                  "<rootDir>/src/common/**/*.test.ts",
                  "<rootDir>/scripts/**/*.test.cjs",
              ],
        transform: {
            "^.+\\.[cm]?[jt]sx?$": [
                join(
                    portal,
                    "node_modules/@backstage/cli/config/jestSwcTransform.js",
                ),
                {
                    jsc: {
                        target: "es2022",
                        parser: { syntax: "typescript", tsx: true },
                        transform: { react: { runtime: "automatic" } },
                    },
                    module: { type: "commonjs" },
                },
            ],
        },
        setupFilesAfterEnv: frontend
            ? [
                  hostRequire.resolve("cross-fetch/polyfill"),
                  "<rootDir>/src/common/setupTests.ts",
              ]
            : [],
        transformIgnorePatterns: frontend
            ? []
            : [
                  "/node_modules/(?!(@backstage|@ansible|@spotify|@backstage-community|lodash-es|nanoid|uuid|jose|@sindresorhus|@octokit|@mswjs|msw)/)",
              ],
        moduleNameMapper: {
            // Shared source tests cross workspace boundaries. Use the host's
            // React, router and BUI instances for providers and rendered controls.
            ...Object.fromEntries(
                (frontend
                    ? [
                          "react",
                          "react/jsx-runtime",
                          "react/jsx-dev-runtime",
                          "react-dom",
                          "react-dom/client",
                          "react-dom/test-utils",
                          "react-router-dom",
                          "@backstage/ui",
                      ]
                    : []
                ).map((name) => [`^${name}$`, hostRequire.resolve(name)]),
            ),
            "\\.(css|scss)$": join(
                portal,
                "node_modules/identity-obj-proxy/src/index.js",
            ),
            "^apme:registration-template$":
                "<rootDir>/templates/apme-register-git-repository/template.yaml",
        },
        modulePathIgnorePatterns: ["<rootDir>/dist/"],
    };
    config.transform["\\.yaml$"] = join(
        portal,
        "node_modules/@backstage/cli/config/jestYamlTransform.js",
    );
    config.transform[
        "\\.(bmp|gif|jpg|jpeg|png|ico|webp|frag|xml|svg|eot|woff|woff2|ttf)$"
    ] = join(portal, "node_modules/@backstage/cli/config/jestFileTransform.js");
    const configPath = join(staging, "config.json");
    writeFileSync(configPath, JSON.stringify(config));
    execFileSync(
        process.execPath,
        [
            join(portal, "node_modules/jest/bin/jest.js"),
            "--config",
            configPath,
            "--runInBand",
            "--forceExit",
            ...process.argv.slice(2).filter((arg) => arg !== "--frontend"),
        ],
        { cwd: join(root, "frontend/portal"), stdio: "inherit" },
    );
} finally {
    rmSync(staging, { recursive: true, force: true });
}
