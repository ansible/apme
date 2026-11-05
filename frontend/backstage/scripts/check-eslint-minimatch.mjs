import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const lock = JSON.parse(
    readFileSync(resolve(root, "package-lock.json"), "utf8"),
);
const lintConsumers = [
    "node_modules/eslint",
    "node_modules/@eslint/config-array",
    "node_modules/@typescript-eslint/typescript-estree",
];

for (const consumer of lintConsumers) {
    const packageEntry = lock.packages[consumer];
    if (!packageEntry?.dependencies?.minimatch)
        throw new Error(`${consumer} no longer declares minimatch`);
    let current = consumer;
    let matcherPath;
    while (current.startsWith("node_modules/")) {
        const candidate =
            current === "node_modules"
                ? "node_modules/minimatch"
                : `${current}/node_modules/minimatch`;
        if (lock.packages[candidate]) {
            matcherPath = candidate;
            break;
        }
        const parent = current.slice(0, current.lastIndexOf("/"));
        current = parent.length <= "node_modules".length ? "" : parent;
    }
    matcherPath ??= "node_modules/minimatch";
    const entry = lock.packages[matcherPath];
    if (!entry)
        throw new Error(`${consumer} has no locked minimatch dependency`);
    const [major, minor, patch] = entry.version.split(".").map(Number);
    if (major !== 11 || (minor === 0 && patch < 1))
        throw new Error(
            `${consumer} resolves ${matcherPath} at minimatch ${entry.version}; update the ESLint override and this security guard together`,
        );
}
