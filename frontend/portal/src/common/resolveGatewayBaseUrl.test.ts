/*
 * Copyright Red Hat
 */

import {
  InvalidGatewayBaseUrlError,
  normalizeGatewayBaseUrl,
  resolveGatewayBaseUrl,
} from "./resolveGatewayBaseUrl";

describe("normalizeGatewayBaseUrl", () => {
  it("returns undefined for empty input", () => {
    expect(normalizeGatewayBaseUrl(undefined)).toBeUndefined();
    expect(normalizeGatewayBaseUrl(null)).toBeUndefined();
    expect(normalizeGatewayBaseUrl("  ")).toBeUndefined();
  });

  it("strips trailing slashes", () => {
    expect(normalizeGatewayBaseUrl("http://localhost:8080/")).toBe(
      "http://localhost:8080",
    );
  });

  it("allows private hosts used by local Gateway installs", () => {
    expect(
      normalizeGatewayBaseUrl("http://host.containers.internal:8080"),
    ).toBe("http://host.containers.internal:8080");
  });

  it("rejects non-http schemes and credentials", () => {
    expect(() => normalizeGatewayBaseUrl("ftp://gateway.example")).toThrow(
      InvalidGatewayBaseUrlError,
    );
    expect(() =>
      normalizeGatewayBaseUrl("https://user:pass@gateway.example"),
    ).toThrow(InvalidGatewayBaseUrlError);
    expect(() =>
      normalizeGatewayBaseUrl("http://localhost:8080/?next=1"),
    ).toThrow(InvalidGatewayBaseUrlError);
  });
});

describe("resolveGatewayBaseUrl", () => {
  it("preserves slash runs in the fallback without regex backtracking", () => {
    const value = `not-a-url${"/".repeat(100_000)}x///`;
    expect(resolveGatewayBaseUrl({ configBaseUrl: value })).toEqual({
      effective: value.slice(0, -3),
      source: "config",
    });
  });

  it("prefers a persisted portal-settings override", () => {
    const result = resolveGatewayBaseUrl({
      configBaseUrl: "http://localhost:8080",
      store: {
        global: { gatewayBaseUrl: "http://host.containers.internal:8080/" },
      },
    });
    expect(result).toEqual({
      effective: "http://host.containers.internal:8080",
      source: "global",
    });
  });

  it("falls back to app-config when the store has no override", () => {
    expect(
      resolveGatewayBaseUrl({
        configBaseUrl: "http://localhost:8080/",
      }),
    ).toEqual({
      effective: "http://localhost:8080",
      source: "config",
    });
  });

  it("ignores a corrupt stored URL and uses app-config", () => {
    expect(
      resolveGatewayBaseUrl({
        configBaseUrl: "http://localhost:8080",
        store: { global: { gatewayBaseUrl: "not-a-url" } },
      }).source,
    ).toBe("config");
  });
});
