import { trimTrailingSlashes } from "./trimTrailingSlashes";

describe("trimTrailingSlashes", () => {
  it.each([
    ["", ""],
    ["///", ""],
    ["https://gateway.example/api///", "https://gateway.example/api"],
    ["https://gateway.example/a//b", "https://gateway.example/a//b"],
  ])("normalizes %j without changing interior slashes", (input, expected) => {
    expect(trimTrailingSlashes(input)).toBe(expected);
  });

  it("preserves a long slash run followed by a non-slash", () => {
    const value = `https://gateway.example/${"/".repeat(100_000)}x`;
    expect(trimTrailingSlashes(value)).toBe(value);
  });

  it("removes a long trailing run", () => {
    expect(
      trimTrailingSlashes(`https://gateway.example${"/".repeat(100_000)}`),
    ).toBe("https://gateway.example");
  });
});
