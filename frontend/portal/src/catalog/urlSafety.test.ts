/*
 * Copyright Red Hat
 */

import { assertSafeAbbenayProviderId, assertSafeHttpUrl } from "./urlSafety";

jest.mock("node:dns/promises", () => ({ lookup: jest.fn() }));

import { lookup } from "node:dns/promises";

const mockLookup = lookup as jest.MockedFunction<typeof lookup>;

describe("urlSafety", () => {
  beforeEach(() => {
    mockLookup.mockResolvedValue([
      { address: "93.184.216.34", family: 4 },
    ] as never);
  });

  it("rejects unsafe provider ids", () => {
    expect(() => assertSafeAbbenayProviderId("bad;id")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("x\nPATH")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("$(curl)")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("my-openrouter")).not.toThrow();
  });

  it("assertSafeHttpUrl rejects private hosts and non-https", async () => {
    await expect(assertSafeHttpUrl("http://evil.com", "url")).rejects.toThrow(
      /https/,
    );
    await expect(
      assertSafeHttpUrl("https://127.0.0.1/x", "url"),
    ).rejects.toThrow(/private/);
    await expect(
      assertSafeHttpUrl("https://galaxy.ansible.com/api/", "url"),
    ).resolves.toHaveProperty("host", "galaxy.ansible.com");
  });

  it.each(["[::1]", "[fc00::1]", "[fe80::1]", "[::ffff:10.0.0.1]"])(
    "rejects private IPv6 literal %s",
    async (host) => {
      await expect(
        assertSafeHttpUrl(`https://${host}/api/`, "url"),
      ).rejects.toThrow(/private/);
    },
  );

  it("rejects DNS names that resolve to private addresses", async () => {
    mockLookup.mockResolvedValueOnce([
      { address: "10.20.30.40", family: 4 },
    ] as never);
    await expect(
      assertSafeHttpUrl("https://packages.example.test/api/", "url"),
    ).rejects.toThrow(/resolve to private/);
  });

  it("allows http and private hosts when opted in", async () => {
    await expect(
      assertSafeHttpUrl("http://apme-gateway:8080/x", "url", {
        allowHttp: true,
        allowPrivateHosts: true,
      }),
    ).resolves.toHaveProperty("hostname", "apme-gateway");
  });
});
