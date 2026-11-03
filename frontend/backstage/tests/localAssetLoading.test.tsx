import { render, screen } from "@testing-library/react";
import { loadLocalPage } from "../../../containers/rhdh/plugin/src/assetLoading";
it("recovers a failed settings CSS chunk with an explicit reload instead of an app crash", async () => {
  const error = new Error("Loading CSS chunk 889 failed.");
  error.name = "ChunkLoadError";
  render(
    await loadLocalPage(async () => {
      throw error;
    }),
  );
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reload APME" })).toBeTruthy();
});
it("does not mask an application error as a stale asset", async () => {
  const error = new Error("Broken configuration");
  await expect(
    loadLocalPage(async () => {
      throw error;
    }),
  ).rejects.toBe(error);
});
