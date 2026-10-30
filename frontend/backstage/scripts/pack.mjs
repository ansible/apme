import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
    cpSync,
    mkdirSync,
    mkdtempSync,
    readFileSync,
    rmSync,
    writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const { values } = parseArgs({
    options: {
        version: { type: "string", default: "0.0.0-dev" },
        output: { type: "string", default: "dist/ui-packages" },
        local: { type: "boolean", default: false },
    },
});
const version = values.version;
if (!/^\d+\.\d+\.\d+(?:-[\da-z.-]+)?$/i.test(version))
    throw new Error("Expected a semver product version");
const output = resolve(root, values.output);
mkdirSync(output, { recursive: true });
const packages = [
    ["@apme/client", "frontend/packages/apme-client"],
    ["@apme/ui-workflow", "frontend/packages/ui-workflow"],
    ["@apme/backstage-plugin", "frontend/backstage/plugins/backstage-apme"],
    [
        "@apme/backstage-backend",
        "frontend/backstage/plugins/backstage-apme-backend",
    ],
];
const filenames = Object.fromEntries(
    packages.map(([name]) => [
        name,
        `${name.slice(1).replace("/", "-")}-${version}.tgz`,
    ]),
);
const artifacts = [];
for (const [name, directory] of packages) {
    const source = join(root, directory);
    const stage = mkdtempSync(join(tmpdir(), "apme-pack-"));
    try {
        const manifest = JSON.parse(
            readFileSync(join(source, "package.json"), "utf8"),
        );
        manifest.version = version;
        // Release packages must not contain local workspace paths or build scripts.
        delete manifest.devDependencies;
        delete manifest.scripts;
        for (const dependency of Object.keys(manifest.dependencies ?? {})) {
            if (filenames[dependency])
                manifest.dependencies[dependency] = values.local
                    ? `file:${join(output, filenames[dependency])}`
                    : `https://github.com/ansible/apme/releases/download/v${version}/${filenames[dependency]}`;
        }
        manifest.files = [...new Set([...(manifest.files ?? []), "LICENSE"])];
        writeFileSync(
            join(stage, "package.json"),
            `${JSON.stringify(manifest, null, 2)}\n`,
        );
        cpSync(join(source, "dist"), join(stage, "dist"), { recursive: true });
        cpSync(join(source, "README.md"), join(stage, "README.md"));
        cpSync(join(root, "LICENSE"), join(stage, "LICENSE"));
        if (manifest.configSchema)
            cpSync(
                join(source, manifest.configSchema),
                join(stage, manifest.configSchema),
            );
        const result = JSON.parse(
            execFileSync(
                "npm",
                [
                    "pack",
                    "--ignore-scripts",
                    "--json",
                    "--pack-destination",
                    output,
                ],
                { cwd: stage, encoding: "utf8" },
            ),
        )[0];
        artifacts.push({
            name,
            file: result.filename,
            sha256: createHash("sha256")
                .update(readFileSync(join(output, result.filename)))
                .digest("hex"),
            bytes: result.size,
        });
    } finally {
        rmSync(stage, { recursive: true, force: true });
    }
}
writeFileSync(
    join(output, "SHA256SUMS"),
    artifacts.map((a) => `${a.sha256}  ${a.file}\n`).join(""),
);
writeFileSync(
    join(output, "manifest.json"),
    `${JSON.stringify(
        {
            version,
            commit: execFileSync("git", ["rev-parse", "HEAD"], {
                cwd: root,
                encoding: "utf8",
            }).trim(),
            node: process.version,
            dependencyMode: values.local ? "local-verification" : "release",
            artifacts,
        },
        null,
        2,
    )}\n`,
);
console.log(JSON.stringify({ output, artifacts }, null, 2));
