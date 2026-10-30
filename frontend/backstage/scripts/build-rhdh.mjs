/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { loadConfigSchema } from "@backstage/config-loader";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const frontend = resolve(root, "containers/rhdh/plugin");
execFileSync(
    process.execPath,
    [
        resolve(root, "node_modules/@red-hat-developer-hub/cli/bin/rhdh-cli"),
        "plugin",
        "export",
    ],
    { cwd: frontend, stdio: "inherit" },
);

// Frontend code and its private dependencies are compiled into federation assets.
// The installer must not try to resolve unpublished workspace packages at startup.
const frontendManifest = resolve(frontend, "dist-dynamic/package.json");
const manifest = JSON.parse(readFileSync(frontendManifest, "utf8"));
delete manifest.dependencies;
delete manifest.devDependencies;
writeFileSync(frontendManifest, `${JSON.stringify(manifest, null, 2)}\n`);

// The standard new-backend-system feature is bundled as CommonJS, keeping the
// host's backend API shared so service/extension identities remain authoritative.
const backend = resolve(
    root,
    "frontend/backstage/plugins/backstage-apme-backend",
);
const output = resolve(backend, "dist-dynamic");
rmSync(output, { recursive: true, force: true });
mkdirSync(resolve(output, "dist"), { recursive: true });
await build({
    entryPoints: [resolve(backend, "src/index.ts")],
    outfile: resolve(output, "dist/index.cjs"),
    bundle: true,
    platform: "node",
    target: "node24",
    format: "cjs",
    external: ["@backstage/backend-plugin-api"],
});
const backendManifest = JSON.parse(
    readFileSync(resolve(backend, "package.json"), "utf8"),
);
const schema = (
    await loadConfigSchema({
        dependencies: [],
        packagePaths: [resolve(backend, "package.json")],
    })
).serialize();
writeFileSync(
    resolve(output, "package.json"),
    `${JSON.stringify(
        {
            name: `${backendManifest.name}-dynamic`,
            version: backendManifest.version,
            license: backendManifest.license,
            backstage: backendManifest.backstage,
            main: "dist/index.cjs",
            files: ["dist"],
            configSchema: "dist/.config-schema.json",
            peerDependencies: backendManifest.peerDependencies,
        },
        null,
        2,
    )}\n`,
);
writeFileSync(
    resolve(output, "dist/.config-schema.json"),
    `${JSON.stringify(schema, null, 2)}\n`,
);
