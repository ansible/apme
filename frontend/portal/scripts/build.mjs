/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { execFileSync } from "node:child_process";
import {
    mkdirSync,
    mkdtempSync,
    readFileSync,
    rmSync,
    cpSync,
    writeFileSync,
} from "node:fs";
import { dirname, join, resolve, relative, sep } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { createRequire } from "node:module";
import { build } from "esbuild";
import { prepareSdk } from "./sdk.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const source = join(root, "frontend/portal");
const { values } = parseArgs({
    options: { "portal-checkout": { type: "string" } },
});
const portal = prepareSdk(values["portal-checkout"]);
const output = join(source, "dist");
rmSync(output, { recursive: true, force: true });
mkdirSync(output, { recursive: true });
const staging = mkdtempSync(join(tmpdir(), "apme-portal-build-"));
try {
    let ancestor = root;
    while (!portal.startsWith(ancestor + sep) && portal !== ancestor)
        ancestor = dirname(ancestor);
    const config = {
        compilerOptions: {
            target: "ES2022",
            module: "ESNext",
            moduleResolution: "bundler",
            lib: ["ES2023", "DOM", "DOM.Iterable"],
            jsx: "react-jsx",
            strict: true,
            skipLibCheck: true,
            esModuleInterop: true,
            useUnknownInCatchVariables: false,
            resolveJsonModule: true,
            declaration: true,
            emitDeclarationOnly: true,
            noEmitOnError: true,
            rootDir: ancestor,
            outDir: join(staging, "types"),
            types: ["node", "react", "react-dom"],
            typeRoots: [join(portal, "node_modules/@types")],
            paths: {
                react: [join(portal, "node_modules/@types/react")],
                "react/*": [join(portal, "node_modules/@types/react/*")],
                "react-dom": [join(portal, "node_modules/@types/react-dom")],
                "react-dom/*": [
                    join(portal, "node_modules/@types/react-dom/*"),
                ],
                "@apme/ui-workflow": [
                    join(root, "frontend/packages/ui-workflow/dist/index.d.ts"),
                ],
                "@apme/backstage-plugin": [
                    join(
                        root,
                        "frontend/backstage/plugins/backstage-apme/dist/index.d.ts",
                    ),
                ],
                "@apme/backstage-plugin-backend": [
                    join(
                        root,
                        "frontend/backstage/plugins/backstage-apme-backend/dist/index.d.ts",
                    ),
                ],
            },
        },
        include: [join(source, "src/**/*.ts"), join(source, "src/**/*.tsx")],
        exclude: [
            join(source, "src/**/*.test.*"),
            join(source, "src/**/setupTests.ts"),
        ],
    };
    const configPath = join(staging, "tsconfig.json");
    writeFileSync(configPath, JSON.stringify(config));
    execFileSync(
        process.execPath,
        [join(root, "node_modules/typescript/bin/tsc"), "-p", configPath],
        { cwd: source, stdio: "inherit" },
    );
    cpSync(
        join(staging, "types", relative(ancestor, join(source, "src"))),
        output,
        { recursive: true },
    );
    const frontend = JSON.parse(
        readFileSync(join(source, "manifests/frontend.json")),
    );
    const backend = JSON.parse(
        readFileSync(join(source, "manifests/backend.json")),
    );
    // Host packages stay external. Relative common contracts are bundled from APME source.
    const external = [
        ...new Set([
            ...Object.keys(frontend.dependencies),
            ...Object.keys(frontend.peerDependencies),
            ...Object.keys(backend.dependencies),
            ...Object.keys(backend.peerDependencies),
        ]),
    ];
    await build({
        entryPoints: {
            "frontend/index": join(source, "src/frontend/index.ts"),
            "frontend/alpha": join(source, "src/frontend/alpha.tsx"),
            "frontend/api/apmeApiFactory": join(
                source,
                "src/frontend/api/apmeApiFactory.ts",
            ),
            "frontend/apmeManifest": join(
                source,
                "src/frontend/apmeManifest.ts",
            ),
        },
        bundle: true,
        splitting: true,
        format: "esm",
        platform: "browser",
        target: "es2022",
        outdir: output,
        external,
        jsx: "automatic",
        loader: { ".css": "css" },
        chunkNames: "frontend/chunks/[name]-[hash]",
        tsconfig: configPath,
    });
    for (const entry of ["catalog", "scaffolder"]) {
        await build({
            entryPoints: [join(source, `src/${entry}/index.ts`)],
            outfile: join(output, entry, "index.cjs"),
            bundle: true,
            format: "cjs",
            platform: "node",
            target: "node24",
            external,
            tsconfig: configPath,
            plugins: [
                {
                    name: "apme-template",
                    setup(builder) {
                        builder.onResolve(
                            { filter: /^apme:registration-template$/ },
                            () => ({
                                path: "registration-template",
                                namespace: "apme-template",
                            }),
                        );
                        builder.onLoad(
                            { filter: /.*/, namespace: "apme-template" },
                            () => {
                                const yaml = createRequire(
                                    join(portal, "package.json"),
                                )("js-yaml");
                                const template = yaml.load(
                                    readFileSync(
                                        join(
                                            source,
                                            "templates/apme-register-git-repository/template.yaml",
                                        ),
                                        "utf8",
                                    ),
                                );
                                return {
                                    contents: `export default ${JSON.stringify(template)}`,
                                    loader: "js",
                                };
                            },
                        );
                    },
                },
            ],
        });
        // Native Backstage imports an ESM default feature. Unwrap the esbuild CJS
        // namespace explicitly so a host never receives { default: feature } instead.
        writeFileSync(
            join(output, entry, "index.js"),
            'import implementation from "./index.cjs";\nexport default implementation.default;\n',
        );
    }
    writeFileSync(
        join(output, "build.json"),
        JSON.stringify(
            {
                portalRevision: execFileSync("git", ["rev-parse", "HEAD"], {
                    cwd: portal,
                    encoding: "utf8",
                }).trim(),
            },
            null,
            2,
        ) + "\n",
    );
    console.log(
        "Portal adapter declarations and frontend/backend bundles built against the real host SDK.",
    );
} finally {
    rmSync(staging, { recursive: true, force: true });
}
