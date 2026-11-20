// @vitest-environment node
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

const root = fileURLToPath(new URL("../../../", import.meta.url));

it.each([
  "@backstage/cli-module-build/node_modules/@ts-morph/common",
  "@typescript-eslint/typescript-estree",
])("%s still matches TypeScript source patterns", (consumer) => {
  const consumerManifest = join(root, "node_modules", consumer, "package.json");
  expect(existsSync(consumerManifest)).toBe(true);
  const require = createRequire(consumerManifest);
  // Both consumers call the named function; minimatch 11 removed makeRe.
  const { minimatch } = require("minimatch") as {
    minimatch: (path: string, pattern: string) => boolean;
  };
  const pattern = "src/**/!(*.test).{ts,tsx}";
  for (const path of ["src/index.ts", "src/nested/Page.tsx"]) {
    expect(minimatch(path, pattern)).toBe(true);
  }
  for (const path of ["src/index.test.ts", "dist/index.ts", "src/.hidden.ts"]) {
    expect(minimatch(path, pattern)).toBe(false);
  }
});

it.each(["@rollup/plugin-commonjs", "@backstage/cli-module-build"])(
  "%s keeps glob discovery, exclusions, and cancellation after the security upgrade",
  async (consumer) => {
    const require = createRequire(
      join(root, "node_modules", consumer, "package.json"),
    );
    const glob = require("glob") as {
      globSync: (
        pattern: string,
        options: { cwd: string; ignore: string },
      ) => string[];
      glob: (
        pattern: string,
        options: { cwd: string; signal: AbortSignal },
      ) => Promise<string[]>;
    };
    const cwd = mkdtempSync(join(tmpdir(), "apme-glob-"));
    try {
      mkdirSync(join(cwd, "src"));
      mkdirSync(join(cwd, "dist"));
      writeFileSync(join(cwd, "src", "index.ts"), "");
      writeFileSync(join(cwd, "src", "index.test.ts"), "");
      writeFileSync(join(cwd, "dist", "index.js"), "");
      expect(
        glob.globSync("**/!(*.test).{ts,js}", { cwd, ignore: "dist/**" }),
      ).toEqual(["src/index.ts"]);
      const controller = new AbortController();
      const reason = new Error("cancel discovery");
      controller.abort(reason);
      await expect(
        glob.glob("**/*", { cwd, signal: controller.signal }),
      ).rejects.toBe(reason);
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  },
);
