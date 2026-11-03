/*
 * Copyright Red Hat
 */

import {
  formatScmAuthFailureMessage,
  isScmTokenRequiredError,
  resolveScmTokenForRemediation,
} from "./resolveScmTokenForRemediation";

describe("resolveScmTokenForRemediation", () => {
  const repoUrl = "https://github.com/acme/ansible-apme";

  it("returns cached token in optional mode without forcing OAuth", async () => {
    const getCredentials = jest
      .fn()
      .mockResolvedValue({ token: " ghp_cached " });
    const token = await resolveScmTokenForRemediation(
      { getCredentials },
      repoUrl,
      "optional",
    );
    expect(token).toBe("ghp_cached");
    expect(getCredentials).toHaveBeenCalledWith({
      url: repoUrl,
      optional: true,
      additionalScope: { repoWrite: true },
    });
  });

  it("returns undefined in optional mode when no cached token", async () => {
    const getCredentials = jest.fn().mockResolvedValue({ token: undefined });
    const token = await resolveScmTokenForRemediation(
      { getCredentials },
      repoUrl,
      "optional",
    );
    expect(token).toBeUndefined();
    expect(getCredentials).toHaveBeenCalledWith(
      expect.objectContaining({ optional: true }),
    );
  });

  it("throws in interactive mode when authorization yields no token", async () => {
    const getCredentials = jest.fn().mockResolvedValue({ token: "" });
    await expect(
      resolveScmTokenForRemediation({ getCredentials }, repoUrl, "interactive"),
    ).rejects.toThrow(/GitHub write access failed/);
    expect(getCredentials).toHaveBeenCalledWith(
      expect.objectContaining({ optional: false }),
    );
  });

  it("throws when repo URL is missing", async () => {
    await expect(
      resolveScmTokenForRemediation(
        { getCredentials: jest.fn() },
        "",
        "optional",
      ),
    ).rejects.toThrow(/No repository URL/);
  });
});

describe("isScmTokenRequiredError", () => {
  it("detects 422 No SCM token", () => {
    expect(
      isScmTokenRequiredError(
        new Error('APME API error: 422 - {"detail":"No SCM token configured"}'),
      ),
    ).toBe(true);
  });

  it("detects OAuth redirect failure text", () => {
    expect(
      isScmTokenRequiredError(
        new Error("Authentication failed, Failed to obtain access token"),
      ),
    ).toBe(true);
  });

  it("detects git login failed from push", () => {
    expect(
      isScmTokenRequiredError(
        new Error(
          "fatal: Authentication failed for ans-tower-devsecops: login failed",
        ),
      ),
    ).toBe(true);
  });

  it("ignores unrelated errors", () => {
    expect(isScmTokenRequiredError(new Error("Network timeout"))).toBe(false);
  });

  it("does not treat project UUIDs containing 401 as auth failures", () => {
    expect(
      isScmTokenRequiredError(
        new Error(
          "APME API request timed out or was aborted: http://localhost:7007/api/catalog/apme/projects/246d7f5366714d54bc5e58c401c1dabf/submit",
        ),
      ),
    ).toBe(false);
  });

  it("still detects real API 401 status codes", () => {
    expect(
      isScmTokenRequiredError(new Error("APME API error: 401 - Unauthorized")),
    ).toBe(true);
  });

  it("detects API 403 status codes", () => {
    expect(
      isScmTokenRequiredError(new Error("APME API error: 403 - Forbidden")),
    ).toBe(true);
  });

  it("detects SCM-related 502 failures", () => {
    expect(
      isScmTokenRequiredError(
        new Error("APME API error: 502 - GitHub SCM token rejected"),
      ),
    ).toBe(true);
  });

  it("ignores non-SCM 502 failures", () => {
    expect(
      isScmTokenRequiredError(new Error("APME API error: 502 - Bad Gateway")),
    ).toBe(false);
  });
});

describe("formatScmAuthFailureMessage", () => {
  it("rewrites OAuth failure into actionable guidance", () => {
    const message = formatScmAuthFailureMessage(
      new Error("Authentication failed, Failed to obtain access token"),
    );
    expect(message).toMatch(/GitHub write access failed/);
    expect(message).toMatch(/Contents and Pull requests write/);
    expect(message).not.toMatch(/public_repo/);
  });

  it("passes through unrelated error messages", () => {
    expect(
      formatScmAuthFailureMessage(new Error("Branch already exists")),
    ).toBe("Branch already exists");
  });

  it("serializes non-Error values without Object stringification", () => {
    expect(formatScmAuthFailureMessage("plain failure")).toBe("plain failure");
    expect(formatScmAuthFailureMessage({ detail: "nope" })).toBe(
      '{"detail":"nope"}',
    );
  });
});
