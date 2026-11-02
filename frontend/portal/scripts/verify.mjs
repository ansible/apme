/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import {
    mkdirSync,
    mkdtempSync,
    readFileSync,
    rmSync,
    symlinkSync,
    writeFileSync,
} from "node:fs";
import { join, resolve, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const { values } = parseArgs({
    options: {
        artifacts: { type: "string", default: "dist/portal-packages" },
        "portal-checkout": { type: "string" },
    },
});
const portal = resolve(
    values["portal-checkout"] ??
        process.env.APME_PORTAL_CHECKOUT ??
        "../ansible-backstage-plugins",
);
const artifacts = resolve(values.artifacts);
const record = JSON.parse(readFileSync(join(artifacts, "manifest.json")));
if (record.dependencyMode !== "local-verification")
    throw new Error("Pack using --local for installation verification");
const temporary = mkdtempSync(join(tmpdir(), "apme-portal-consumer-"));
try {
    const host = JSON.parse(readFileSync(join(root, "package.json")));
    const dependencies = {
        ...host.dependencies,
        "@backstage/frontend-plugin-api": "0.18.0",
        "@types/react": host.devDependencies["@types/react"],
        "@types/react-dom": host.devDependencies["@types/react-dom"],
        "@types/node": host.devDependencies["@types/node"],
        ...Object.fromEntries(
            record.artifacts.map((a) => [
                a.name,
                `file:${join(artifacts, a.file)}`,
            ]),
        ),
    };
    writeFileSync(
        join(temporary, "package.json"),
        JSON.stringify({ private: true, type: "module", dependencies }),
    );
    execFileSync(
        "npm",
        [
            "install",
            "--legacy-peer-deps",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        { cwd: temporary, stdio: "inherit" },
    );
    for (const name of [
        "portal-extension-api",
        "portal-extension-host",
        "portal-extension-common",
        "portal-plugin-node",
        "backstage-rhaap-common",
    ]) {
        mkdirSync(join(temporary, "node_modules/@ansible"), {
            recursive: true,
        });
        symlinkSync(
            join(portal, "node_modules/@ansible", name),
            join(temporary, "node_modules/@ansible", name),
            "dir",
        );
    }
    for (const artifact of record.artifacts) {
        const manifest = JSON.parse(
            readFileSync(
                join(temporary, "node_modules", artifact.name, "package.json"),
            ),
        );
        if (
            Object.values(manifest.peerDependencies ?? {}).some((v) =>
                v.startsWith("workspace:"),
            )
        )
            throw new Error("Workspace peer in packed artifact");
        if (!artifact.name.startsWith("@apme/portal-")) {
            const deps = Object.keys({
                ...manifest.dependencies,
                ...manifest.peerDependencies,
            });
            if (deps.some((name) => name.startsWith("@ansible/")))
                throw new Error(
                    "Portal dependency leaked into a standalone package",
                );
        }
    }
    const frontend = join(temporary, "frontend.ts");
    writeFileSync(
        frontend,
        "import plugin from '@apme/portal-plugin/alpha'; import {apmeManifest} from '@apme/portal-plugin/manifest'; import standalone from '@apme/backstage-plugin/alpha'; Object.assign(window,{plugin,apmeManifest,standalone});",
    );
    await build({
        entryPoints: [frontend],
        bundle: true,
        format: "esm",
        platform: "browser",
        target: "es2022",
        outfile: join(temporary, "frontend.js"),
        loader: {
            ".css": "css",
            ".woff2": "file",
            ".woff": "file",
            ".ttf": "file",
            ".svg": "file",
            ".png": "file",
            ".jpg": "file",
            ".eot": "file",
        },
        alias: {
            react: join(temporary, "node_modules/react"),
            "react-dom": join(temporary, "node_modules/react-dom"),
        },
    });
    const backend = join(temporary, "backend.ts");
    writeFileSync(
        backend,
        "import catalog from '@apme/portal-plugin-backend/catalog'; import scaffolder from '@apme/portal-plugin-backend/scaffolder'; if(catalog.$$type !== '@backstage/BackendFeature' || scaffolder.$$type !== '@backstage/BackendFeature') throw new Error('Missing backend feature'); console.log('Packed catalog and scaffolder features load');",
    );
    // Bundle SDK peers exactly as the Portal backend build does; private source peers
    // in this fixture are TypeScript rather than independently published packages.
    await build({
        entryPoints: [backend],
        bundle: true,
        format: "cjs",
        platform: "node",
        target: "node24",
        outfile: join(temporary, "backend.cjs"),
        external: [
            "@backstage/*",
            "express",
            "express-promise-router",
            "undici",
            "ajv",
        ],
        alias: {
            "@ansible/portal-plugin-node": join(
                portal,
                "workspaces/portal-core/plugins/portal-plugin-node/src/index.ts",
            ),
        },
    });
    execFileSync(process.execPath, [join(temporary, "backend.cjs")], {
        cwd: temporary,
        stdio: "inherit",
    });
    execFileSync(
        process.execPath,
        [
            join(root, "node_modules/typescript/bin/tsc"),
            "--noEmit",
            "--skipLibCheck",
            "--strict",
            "--moduleResolution",
            "bundler",
            "--module",
            "ESNext",
            "--target",
            "ES2023",
            "--jsx",
            "react-jsx",
            frontend,
        ],
        { cwd: temporary, stdio: "inherit" },
    );
    console.log(
        "Packed Portal/standalone frontend browser bundle, backend features and public declarations passed.",
    );
} finally {
    rmSync(temporary, { recursive: true, force: true });
}
