import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const lock = JSON.parse(
    readFileSync(resolve(root, "package-lock.json"), "utf8"),
);
const matchers = Object.entries(lock.packages).filter(([path]) =>
    path.endsWith("node_modules/minimatch"),
);
if (matchers.length === 0)
    throw new Error(
        "The UI lint dependency graph has no locked minimatch package",
    );

for (const [path, entry] of matchers) {
    const [major, minor, patch] = entry.version.split(".").map(Number);
    if (major !== 11 || (minor === 0 && patch < 1))
        throw new Error(
            `${path} resolves minimatch ${entry.version}; update the ESLint override and this security guard together`,
        );
}
