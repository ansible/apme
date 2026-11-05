import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const artifacts = join(root, "dist/ui-packages");
const record = JSON.parse(
    readFileSync(join(artifacts, "manifest.json"), "utf8"),
);
if (record.dependencyMode !== "local-verification")
    throw new Error("Run frontend-pack -- --local before this smoke check");
if ("node" in record)
    throw new Error(
        "Package manifest must not include the runner's Node version",
    );
const temp = mkdtempSync(join(tmpdir(), "apme-foundation-consumer-"));
try {
    const manifest = JSON.parse(
        readFileSync(join(root, "package.json"), "utf8"),
    );
    const dependencies = { ...manifest.dependencies };
    for (const artifact of record.artifacts)
        dependencies[artifact.name] = `file:${join(artifacts, artifact.file)}`;
    const reactTypes = JSON.parse(
        readFileSync(join(root, "package.json"), "utf8"),
    ).devDependencies["@types/react"];
    writeFileSync(
        join(temp, "package.json"),
        JSON.stringify({
            private: true,
            type: "module",
            dependencies,
            devDependencies: { "@types/react": reactTypes },
        }),
    );
    execFileSync(
        "npm",
        ["install", "--ignore-scripts", "--no-audit", "--no-fund"],
        { cwd: temp, stdio: "inherit" },
    );
    const lock = readFileSync(join(temp, "package-lock.json"), "utf8");
    if (/portal-(?:extension|plugin)|backstage-rhaap|workspace:/.test(lock))
        throw new Error(
            "Standalone dependency closure includes Portal/AAP/workspace packages",
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
    // Bundle the installed tarballs, with no source aliases or repository imports.
    writeFileSync(
        join(temp, "main.ts"),
        "import {ApmeProjectWorkflow,ApmeProjectFindings,ApmeProjectHistory,ApmeActivityDetails,ApmeProjectDependencies,ApmeProjectGraph,ApmeProjectSbom,createPortalProjectApiAdapter,createPortalApmeApiAdapter,apmePlugin,type ApmeProjectWorkflowProps} from '@apme/backstage-plugin'; import {useProjectWorkflow} from '@apme/ui-workflow/headless'; import {createDefaultApmeApiAdapter} from '@apme/client'; const props: ApmeProjectWorkflowProps = {projectId:'repo',adapter:createDefaultApmeApiAdapter()}; Object.assign(window,{apme:{ApmeProjectWorkflow,ApmeProjectFindings,ApmeProjectHistory,ApmeActivityDetails,ApmeProjectDependencies,ApmeProjectGraph,ApmeProjectSbom,createPortalProjectApiAdapter,createPortalApmeApiAdapter,apmePlugin,useProjectWorkflow,props}});",
    );
    // Type-check only the installed public declarations, without source aliases.
    const views = [
        ["workflow", "ApmeProjectWorkflow"],
        ["findings", "ApmeProjectFindings"],
        ["activity", "ApmeActivityDetails"],
        ["dependencies", "ApmeProjectDependencies"],
        ["graph", "ApmeProjectGraph"],
    ];
    writeFileSync(
        join(temp, "main.ts"),
        readFileSync(join(temp, "main.ts"), "utf8") +
            views
                .map(
                    ([name, component]) =>
                        `\nimport {${component} as ${name}View, capability as ${name}Capability} from '@apme/backstage-plugin/${name}'; Object.assign(window,{${name}View,${name}Capability});`,
                )
                .join("") +
            "\nimport {apmeProjectViews,type ApmeProjectView} from '@apme/backstage-plugin/capabilities'; const selectedViews: readonly ApmeProjectView[] = ['activity','findings']; Object.assign(window,{apmeProjectViews,selectedViews});",
    );
    execFileSync(
        process.execPath,
        [
            join(root, "node_modules/typescript/bin/tsc"),
            "--noEmit",
            "--strict",
            "--moduleResolution",
            "bundler",
            "--module",
            "ESNext",
            "--lib",
            "ES2021,DOM,DOM.Iterable",
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
        "Clean tarball install, Node client import, public declarations, and browser package bundle passed.",
    );
} finally {
    rmSync(temp, { recursive: true, force: true });
}
