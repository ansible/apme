/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";

/** Retry partial uploads without replacing already published immutable bytes. */
export function uploadRelease(tag, directory, gh = execFileSync) {
    if (!/^v\d+\.\d+\.\d+$/.test(tag))
        throw new Error("Expected a vX.Y.Z release tag");
    const manifest = JSON.parse(
        readFileSync(join(directory, "manifest.json"), "utf8"),
    );
    if (
        manifest.version !== tag.slice(1) ||
        manifest.dependencyMode !== "release"
    )
        throw new Error(
            "Release tag and immutable package manifest must match",
        );
    const files = [
        ...manifest.artifacts.map((a) => a.file),
        "SHA256SUMS",
        "manifest.json",
    ];
    if (files.some((file) => basename(file) !== file))
        throw new Error("Invalid release asset name");
    const digest = (data) => createHash("sha256").update(data).digest("hex");
    for (const artifact of manifest.artifacts)
        if (
            digest(readFileSync(join(directory, artifact.file))) !==
            artifact.sha256
        )
            throw new Error(`Artifact checksum mismatch: ${artifact.file}`);
    const release = JSON.parse(
        gh("gh", ["release", "view", tag, "--json", "assets"], {
            encoding: "utf8",
        }),
    );
    const existing = new Set(release.assets.map((asset) => asset.name));
    const staging = mkdtempSync(join(tmpdir(), "apme-release-verify-"));
    try {
        const missing = [];
        for (const file of files) {
            if (!existing.has(file)) {
                missing.push(join(directory, file));
                continue;
            }
            gh(
                "gh",
                [
                    "release",
                    "download",
                    tag,
                    "--pattern",
                    file,
                    "--dir",
                    staging,
                ],
                { stdio: "inherit" },
            );
            if (
                digest(readFileSync(join(staging, file))) !==
                digest(readFileSync(join(directory, file)))
            )
                throw new Error(
                    `Published asset differs; refusing to replace immutable ${file}`,
                );
        }
        if (missing.length)
            gh("gh", ["release", "upload", tag, ...missing], {
                stdio: "inherit",
            });
    } finally {
        rmSync(staging, { recursive: true, force: true });
    }
}

if (
    process.argv[1] &&
    fileURLToPath(import.meta.url) === resolve(process.argv[1])
) {
    const { values } = parseArgs({
        options: {
            tag: { type: "string" },
            artifacts: { type: "string", default: "dist/ui-packages" },
        },
    });
    uploadRelease(values.tag ?? "", resolve(values.artifacts));
}
