// @vitest-environment node
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import ts from "typescript";

function runtimeImports(entry: string, seen = new Set<string>()): string[] {
  const file = resolve(entry);
  if (seen.has(file)) return [];
  seen.add(file);
  const source = ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.Latest,
  );
  const dependencies: string[] = [];
  for (const node of source.statements) {
    if (!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node))
      continue;
    if (!node.moduleSpecifier || !ts.isStringLiteral(node.moduleSpecifier))
      continue;
    if (
      (ts.isExportDeclaration(node) && node.isTypeOnly) ||
      (ts.isImportDeclaration(node) && node.importClause?.isTypeOnly)
    )
      continue;
    const path = node.moduleSpecifier.text;
    dependencies.push(path);
    if (path.startsWith(".")) {
      const base = resolve(dirname(file), path.replace(/\.js$/, ""));
      const target = [`${base}.ts`, `${base}.tsx`, `${base}/index.ts`].find(
        existsSync,
      );
      if (target) dependencies.push(...runtimeImports(target, seen));
    }
  }
  return dependencies;
}

it("keeps the Gateway client independent of React, UI and host packages", () => {
  const dependencies = runtimeImports(
    "frontend/packages/apme-client/src/index.ts",
  );
  expect(dependencies.filter((path) => !path.startsWith("."))).toEqual([]);
});

it("keeps the headless workflow import free of CSS and UI implementation", () => {
  const dependencies = runtimeImports(
    "frontend/packages/ui-workflow/src/headless.ts",
  );
  expect(
    dependencies.some((path) =>
      /\.css|patternfly|@backstage|@ansible|\/components\//.test(path),
    ),
  ).toBe(false);
});

it("does not resolve private Portal/AAP packages in the standard workspace", () => {
  const lock = readFileSync("package-lock.json", "utf8");
  expect(lock).not.toMatch(
    /portal-(?:extension|plugin)|backstage-rhaap|workspace:/,
  );
});
