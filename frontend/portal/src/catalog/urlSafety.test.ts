/*
 * Copyright Red Hat
 */

import { assertSafeAbbenayProviderId, assertSafeHttpUrl } from "./urlSafety";

describe("urlSafety", () => {
  it("rejects unsafe provider ids", () => {
    expect(() => assertSafeAbbenayProviderId("bad;id")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("x\nPATH")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("$(curl)")).toThrow(/must match/);
    expect(() => assertSafeAbbenayProviderId("my-openrouter")).not.toThrow();
  });

  it("assertSafeHttpUrl rejects private hosts and non-https", () => {
    expect(() => assertSafeHttpUrl("http://evil.com", "url")).toThrow(/https/);
    expect(() => assertSafeHttpUrl("https://127.0.0.1/x", "url")).toThrow(
      /private/,
    );
    expect(
      assertSafeHttpUrl("https://galaxy.ansible.com/api/", "url").host,
    ).toBe("galaxy.ansible.com");
  });

  it("allows http and private hosts when opted in", () => {
    expect(
      assertSafeHttpUrl("http://apme-gateway:8080/x", "url", {
        allowHttp: true,
        allowPrivateHosts: true,
      }).hostname,
    ).toBe("apme-gateway");
  });
});
