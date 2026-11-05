// @vitest-environment node
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

it("rejects package output outside the checkout, including a symlinked parent", () => {
  const outside = mkdtempSync(join(tmpdir(), "apme-pack-outside-"));
  mkdirSync(resolve("dist"), { recursive: true });
  const directory = mkdtempSync(join(resolve("dist"), "pack-boundary-"));
  try {
    symlinkSync(outside, join(directory, "escape"), "dir");
    for (const output of [
      outside,
      "../apme-pack-escape",
      join(directory, "escape/new"),
    ]) {
      expect(() =>
        execFileSync(
          process.execPath,
          ["frontend/backstage/scripts/pack.mjs", "--output", output],
          { stdio: "pipe" },
        ),
      ).toThrow();
    }
    expect(() => readFileSync(join(outside, "manifest.json"))).toThrow();
  } finally {
    rmSync(directory, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

it("retries a partial release by verifying existing bytes and uploading only missing assets", async () => {
  const { uploadRelease } = await import(
    pathToFileURL(resolve("frontend/backstage/scripts/upload-release.mjs")).href
  );
  const directory = mkdtempSync(join(tmpdir(), "apme-release-retry-"));
  try {
    const file = "apme-client-1.2.3.tgz";
    const data = Buffer.from("packed plugin");
    const sha256 = createHash("sha256").update(data).digest("hex");
    writeFileSync(join(directory, file), data);
    writeFileSync(
      join(directory, "manifest.json"),
      JSON.stringify({
        version: "1.2.3",
        dependencyMode: "release",
        artifacts: [{ file, sha256 }],
      }),
    );
    writeFileSync(join(directory, "SHA256SUMS"), `${sha256}  ${file}\n`);
    const published = new Map<string, Buffer>([[file, data]]);
    const upload = vi.fn();
    const gh = (_command: string, args: string[]) => {
      if (args[1] === "view")
        return JSON.stringify({
          assets: [...published.keys()].map((name) => ({ name })),
        });
      if (args[1] === "download") {
        const name = args[args.indexOf("--pattern") + 1]!;
        writeFileSync(
          join(args[args.indexOf("--dir") + 1]!, name),
          published.get(name)!,
        );
      } else if (args[1] === "upload") upload(args);
      return "";
    };
    uploadRelease("v1.2.3", directory, gh);
    expect(upload).toHaveBeenCalledWith([
      "release",
      "upload",
      "v1.2.3",
      join(directory, "SHA256SUMS"),
      join(directory, "manifest.json"),
    ]);
    upload.mockClear();
    for (const name of ["SHA256SUMS", "manifest.json"])
      published.set(name, readFileSync(join(directory, name)));
    uploadRelease("v1.2.3", directory, gh);
    expect(upload).not.toHaveBeenCalled();
    published.set(file, Buffer.from("different published artifact"));
    expect(() => uploadRelease("v1.2.3", directory, gh)).toThrow(
      "refusing to replace immutable",
    );
    expect(upload).not.toHaveBeenCalled();
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
