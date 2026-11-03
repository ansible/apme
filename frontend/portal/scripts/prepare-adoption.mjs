/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import {
    mkdirSync,
    mkdtempSync,
    readFileSync,
    rmSync,
    writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { parseArgs } from "node:util";

const { values } = parseArgs({
    options: {
        version: { type: "string" },
        "portal-checkout": { type: "string" },
        artifacts: { type: "string" },
        output: { type: "string", default: "dist/portal-adoption.patch" },
    },
});
if (!values.version || !/^\d+\.\d+\.\d+(?:-[\da-z.-]+)?$/i.test(values.version))
    throw new Error("Supply --version for the APME release to adopt");
const portal = resolve(
    values["portal-checkout"] ??
        process.env.APME_PORTAL_CHECKOUT ??
        "../ansible-backstage-plugins",
);
const output = resolve(values.output);
const packageUrl = (name) => {
    const file = `${name.slice(1).replace("/", "-")}-${values.version}.tgz`;
    return values.artifacts
        ? `file:${join(resolve(values.artifacts), file)}`
        : `https://github.com/ansible/apme/releases/download/v${values.version}/${file}`;
};
const changes = new Map();
const replacements = {
    "@ansible/plugin-backstage-apme": "@apme/portal-plugin",
    "@ansible/backstage-plugin-catalog-backend-module-apme":
        "@apme/portal-plugin-backend/catalog",
    "@ansible/scaffolder-backend-module-apme":
        "@apme/portal-plugin-backend/scaffolder",
};
for (const file of [
    "workspaces/portal-core/packages/app/src/composition.ts",
    "workspaces/portal-core/packages/app/src/App.tsx",
    "workspaces/portal-core/packages/app/src/apis.ts",
    "workspaces/portal-core/packages/backend/src/index.ts",
    "scripts/portal-development.cjs",
]) {
    let content = readFileSync(join(portal, file), "utf8");
    for (const [before, after] of Object.entries(replacements))
        content = content.replaceAll(before, after);
    if (file.endsWith("/apis.ts"))
        content = content.replace(
            "from '@apme/portal-plugin'",
            "from '@apme/portal-plugin/api'",
        );
    changes.set(file, content);
}
for (const kind of ["app", "backend"]) {
    const file = `workspaces/portal-core/packages/${kind}/package.json`;
    const manifest = JSON.parse(readFileSync(join(portal, file)));
    if (kind === "app") {
        delete manifest.dependencies["@ansible/plugin-backstage-apme"];
        manifest.dependencies["@apme/portal-plugin"] = packageUrl(
            "@apme/portal-plugin",
        );
        manifest.dependencies["@ansible/backstage-rhaap-common"] =
            "workspace:^";
        manifest.dependencies["@backstage/backend-plugin-api"] = "1.10.0";
    } else {
        delete manifest.dependencies[
            "@ansible/backstage-plugin-catalog-backend-module-apme"
        ];
        delete manifest.dependencies["@ansible/scaffolder-backend-module-apme"];
        manifest.dependencies["@apme/portal-plugin-backend"] = packageUrl(
            "@apme/portal-plugin-backend",
        );
    }
    changes.set(file, JSON.stringify(manifest, null, 2) + "\n");
}
// APME is composed statically into Portal. Do not point RHDH at nonexistent
// dynamic exports or reintroduce the removed legacy registration side effects.
changes.set(
    "workspaces/portal-core/dynamic-plugins.portal-extension.dev.yaml",
    `# Portal SDK singleton provider for the development overlay.\n# APME-owned tarballs are installed and composed statically through\n# composition.ts and backend.add; they have no separate dynamic overlay entries.\nplugins:\n  - package: ./local-plugins/portal-dev/portal-core\n    disabled: false\n    sharedPackages:\n      - '@ansible/portal-extension-api'\n      - '@ansible/portal-extension-host'\n`,
);
const staging = mkdtempSync(join(tmpdir(), "apme-portal-adoption-"));
try {
    let patch = "";
    for (const [file, content] of changes) {
        const before = join(staging, "before", file);
        const after = join(staging, "after", file);
        mkdirSync(dirname(before), { recursive: true });
        mkdirSync(dirname(after), { recursive: true });
        writeFileSync(before, readFileSync(join(portal, file)));
        writeFileSync(after, content);
        let diff;
        try {
            diff = execFileSync(
                "git",
                ["diff", "--no-index", "--", before, after],
                { encoding: "utf8" },
            );
        } catch (error) {
            if (error.status !== 1) throw error;
            diff = error.stdout;
        }
        patch += diff
            .replaceAll(join(staging, "before") + "/", "/")
            .replaceAll(join(staging, "after") + "/", "/");
    }
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, patch);
    console.log(
        `Prepared ${output}. Apply after release; update Yarn's lockfile and verify the host before deleting workspaces/apme.`,
    );
} finally {
    rmSync(staging, { recursive: true, force: true });
}
