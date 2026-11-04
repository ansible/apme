import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { parseArgs } from "node:util";

const root = resolve(import.meta.dirname, "../../..");
const artifactsDir = join(root, "dist/ui-packages");
const { values } = parseArgs({ options: { version: { type: "string" } } });
if (!values.version || !/^\d+\.\d+\.\d+$/.test(values.version))
    throw new Error("Expected a stable release version in X.Y.Z format");

const manifest = JSON.parse(
    readFileSync(join(artifactsDir, "manifest.json"), "utf8"),
);
const expectedCommit = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
}).trim();
if (
    manifest.version !== values.version ||
    manifest.dependencyMode !== "release" ||
    manifest.commit !== expectedCommit
)
    throw new Error(
        "Release manifest version, mode, or source commit is incorrect",
    );
const expectedPackages = new Map([
    ["@apme/client", `apme-client-${values.version}.tgz`],
    ["@apme/ui-workflow", `apme-ui-workflow-${values.version}.tgz`],
    ["@apme/backstage-plugin", `apme-backstage-plugin-${values.version}.tgz`],
    [
        "@apme/backstage-plugin-backend",
        `apme-backstage-plugin-backend-${values.version}.tgz`,
    ],
]);
if (
    !Array.isArray(manifest.artifacts) ||
    manifest.artifacts.length !== expectedPackages.size
)
    throw new Error("Release manifest has an incomplete package artifact set");

const artifacts = new Map();
const artifactFiles = new Set();
for (const artifact of manifest.artifacts) {
    if (expectedPackages.get(artifact.name) !== artifact.file)
        throw new Error(`Unexpected release artifact: ${artifact.name}`);
    if (artifacts.has(artifact.name) || artifactFiles.has(artifact.file))
        throw new Error(`Duplicate release artifact: ${artifact.name}`);
    artifactFiles.add(artifact.file);
    const path = join(artifactsDir, artifact.file);
    if (!existsSync(path))
        throw new Error(`Missing release artifact: ${artifact.file}`);
    const bytes = readFileSync(path);
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    if (sha256 !== artifact.sha256 || bytes.length !== artifact.bytes)
        throw new Error(
            `Release artifact metadata does not match ${artifact.file}`,
        );
    artifacts.set(artifact.name, artifact);
}
if ([...expectedPackages.keys()].some((name) => !artifacts.has(name)))
    throw new Error("Release manifest is missing a required package artifact");

const expectedChecksums = [...artifacts.values()]
    .map((artifact) => `${artifact.sha256}  ${artifact.file}\n`)
    .join("");
if (
    readFileSync(join(artifactsDir, "SHA256SUMS"), "utf8") !== expectedChecksums
)
    throw new Error("SHA256SUMS does not match the release artifacts");

for (const artifact of artifacts.values()) {
    const packageJson = JSON.parse(
        execFileSync(
            "tar",
            ["-xOf", join(artifactsDir, artifact.file), "package/package.json"],
            { encoding: "utf8" },
        ),
    );
    if (
        packageJson.name !== artifact.name ||
        packageJson.version !== values.version
    )
        throw new Error(`Package identity does not match ${artifact.file}`);

    for (const section of [
        "dependencies",
        "peerDependencies",
        "optionalDependencies",
    ]) {
        for (const [name, version] of Object.entries(
            packageJson[section] ?? {},
        )) {
            if (/^(workspace:|file:)/.test(version))
                throw new Error(
                    `${artifact.name} has a local ${section} dependency: ${name}`,
                );
            if (name.startsWith("@apme/")) {
                const dependency = artifacts.get(name);
                const expected = dependency
                    ? `https://github.com/ansible/apme/releases/download/v${values.version}/${dependency.file}`
                    : undefined;
                if (!expected || version !== expected)
                    throw new Error(
                        `${artifact.name} has an invalid same-release dependency URL for ${name}`,
                    );
            }
        }
    }
}

console.log(
    `Release manifest verified for ${artifacts.size} package artifacts at v${values.version}`,
);
