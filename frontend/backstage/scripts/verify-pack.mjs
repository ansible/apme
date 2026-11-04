import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { build } from "vite";

const root = resolve(import.meta.dirname, "../../..");
const artifacts = join(root, "dist/ui-packages");
const record = JSON.parse(
    readFileSync(join(artifacts, "manifest.json"), "utf8"),
);
if (record.dependencyMode !== "local-verification")
    throw new Error("Pack with --local first");
if ("node" in record)
    throw new Error(
        "Package manifest must not include the runner's Node version",
    );
const temp = mkdtempSync(join(tmpdir(), "apme-foundation-consumer-"));
try {
    const dependencies = {
        ...JSON.parse(readFileSync(join(root, "package.json"))).dependencies,
    };
    for (const artifact of record.artifacts)
        dependencies[artifact.name] = `file:${join(artifacts, artifact.file)}`;
    writeFileSync(
        join(temp, "package.json"),
        JSON.stringify({ private: true, type: "module", dependencies }),
    );
    execFileSync(
        "npm",
        ["install", "--ignore-scripts", "--no-audit", "--no-fund"],
        { cwd: temp, stdio: "inherit" },
    );
    execFileSync(
        process.execPath,
        [
            "--input-type=module",
            "-e",
            "import {createDefaultApmeApiAdapter} from '@apme/client'; if(createDefaultApmeApiAdapter().apiBase !== '/api/v1') process.exit(1)",
        ],
        { cwd: temp, stdio: "inherit" },
    );
    writeFileSync(
        join(temp, "main.ts"),
        "import {useProjectWorkflow} from '@apme/ui-workflow/headless'; import {GraphVisualization} from '@apme/ui-workflow/graph'; Object.assign(window,{useProjectWorkflow,GraphVisualization});",
    );
    execFileSync(
        process.execPath,
        [
            join(root, "node_modules/typescript/bin/tsc"),
            "--noEmit",
            "--strict",
            "--skipLibCheck",
            "--moduleResolution",
            "bundler",
            "--module",
            "ESNext",
            "--target",
            "ES2020",
            "main.ts",
        ],
        { cwd: temp, stdio: "inherit" },
    );
    await build({
        root: temp,
        configFile: false,
        build: {
            outDir: "dist",
            rollupOptions: { input: join(temp, "main.ts") },
        },
    });
    console.log(
        "Packed client/workflow declarations and browser bundle passed",
    );
} finally {
    rmSync(temp, { recursive: true, force: true });
}
