/*
 * Copyright Red Hat
 */

import {
  APME_GATEWAY_UNAVAILABLE_MESSAGE,
  APME_REMEDIATE_CONNECTION_TITLE,
  apmeRemediationErrorTitle,
  extractExistingPrUrlFromError,
  formatApmeUserFacingError,
  isApmeConnectionError,
  parseExistingPrFromError,
  prNumberFromUrl,
} from "./apmeConnectionError";

describe("isApmeConnectionError", () => {
  it("detects fetch failed / failed to connect", () => {
    expect(
      isApmeConnectionError("Failed to connect to APME: fetch failed"),
    ).toBe(true);
    expect(
      isApmeConnectionError(
        "APME API error: 400 - Failed to connect to APME: fetch failed",
      ),
    ).toBe(true);
  });

  it("detects econnrefused, network error, and aborted", () => {
    expect(isApmeConnectionError("connect ECONNREFUSED 127.0.0.1:8080")).toBe(
      true,
    );
    expect(isApmeConnectionError("Network error while calling gateway")).toBe(
      true,
    );
    expect(isApmeConnectionError("This operation was aborted")).toBe(true);
  });

  it("ignores unrelated errors", () => {
    expect(isApmeConnectionError("No automated patches")).toBe(false);
  });
});

describe("formatApmeUserFacingError", () => {
  it("maps connection failures to the service unavailable message", () => {
    const raw =
      'APME API error: 400 - {\n  "error": {\n    "name": "InputError",\n    "message": "Failed to connect to APME: fetch failed",\n    "stack": "InputError: Failed to connect to APME: fetch failed\\n    at ApmeClient.executeRequest"\n  }\n}';
    expect(formatApmeUserFacingError(raw)).toBe(
      APME_GATEWAY_UNAVAILABLE_MESSAGE,
    );
  });

  it("maps empty input to the service unavailable message", () => {
    expect(formatApmeUserFacingError("")).toBe(
      APME_GATEWAY_UNAVAILABLE_MESSAGE,
    );
    expect(formatApmeUserFacingError("   ")).toBe(
      APME_GATEWAY_UNAVAILABLE_MESSAGE,
    );
  });

  it("shortens long JSON APME API error bodies", () => {
    expect(
      formatApmeUserFacingError(
        'APME API error: 500 - {"error":{"name":"Error","detail":"opaque gateway payload without message"}}',
      ),
    ).toBe(
      "Request failed (500). Try again, or check the modernization service if the problem continues.",
    );
  });

  it("prefers nested JSON message when present", () => {
    expect(
      formatApmeUserFacingError(
        'APME API error: 500 - {"error":{"name":"Error","message":"boom","stack":"x"}}',
      ),
    ).toBe("boom");
  });

  it("decodes escaped newlines and quotes in nested JSON messages", () => {
    expect(
      formatApmeUserFacingError(
        'APME API error: 500 - {"error":{"message":"line1\\nline2\\"quoted\\""}}',
      ),
    ).toBe('line1 line2"quoted"');
  });

  it("keeps short APME API error bodies with status", () => {
    expect(
      formatApmeUserFacingError("APME API error: 400 - Branch missing"),
    ).toBe("Request failed (400): Branch missing");
  });

  it("maps remediate-activity text in API error suffix", () => {
    expect(
      formatApmeUserFacingError(
        "APME API error: 400 - Submit requires a remediate activity",
      ),
    ).toMatch(/remediation run/i);
  });

  it("maps stack-heavy raw errors to a safe generic message", () => {
    expect(
      formatApmeUserFacingError(
        "Something exploded\n    at ApmeClient.executeRequest (ApmeClient.ts:149)\n    at async submitRemediation",
      ),
    ).toBe(
      "Something went wrong preparing fixes. Try again, or check the modernization service if the problem continues.",
    );
  });

  it("keeps short non-connection messages", () => {
    expect(formatApmeUserFacingError("Branch already exists")).toBe(
      "Branch already exists",
    );
  });

  it("unwraps JSON detail for remediate-activity errors", () => {
    expect(
      formatApmeUserFacingError(
        '{"detail":"Submit requires a remediate activity"}',
      ),
    ).toMatch(/remediation run/i);
  });

  it("rewrites PR-already-created errors with the PR URL", () => {
    expect(
      formatApmeUserFacingError(
        '{"detail":"PR already created for this activity: https://github.com/acme-scm/apme/pull/1"}',
      ),
    ).toMatch(/already exists[\s\S]*pull\/1/i);
  });
});

describe("prNumberFromUrl", () => {
  it("extracts GitHub and GitLab ids and ignores unrelated URLs", () => {
    expect(prNumberFromUrl("https://github.com/acme/apme/pull/12")).toBe(12);
    expect(
      prNumberFromUrl("https://gitlab.com/acme/apme/-/merge_requests/9"),
    ).toBe(9);
    expect(prNumberFromUrl("https://github.com/acme/apme")).toBeUndefined();
  });

  it("does not match PR numbers embedded in query strings", () => {
    expect(
      prNumberFromUrl("https://github.com/acme/apme/issues/1?next=/pull/12"),
    ).toBeUndefined();
  });

  it("does not match suffixes like /pull/12draft", () => {
    expect(
      prNumberFromUrl("https://github.com/acme/apme/pull/12draft"),
    ).toBeUndefined();
  });

  it("matches /pull/12/ with trailing slash", () => {
    expect(prNumberFromUrl("https://github.com/acme/apme/pull/12/")).toBe(12);
  });

  it("does not treat an unrelated path with an embedded pull segment as a PR", () => {
    expect(
      prNumberFromUrl("https://github.com/acme/apme/issues/1/pull/12"),
    ).toBeUndefined();
  });

  it("extracts GitLab nested-group merge request ids", () => {
    expect(
      prNumberFromUrl(
        "https://gitlab.com/my-group/subgroup/my-repo/-/merge_requests/9",
      ),
    ).toBe(9);
  });
});

describe("parseExistingPrFromError", () => {
  it("extracts GitHub PR URL and number from detail JSON", () => {
    expect(
      parseExistingPrFromError(
        '{"detail":"PR already created for this activity: https://github.com/acme-scm/apme/pull/1"}',
      ),
    ).toEqual({
      url: "https://github.com/acme-scm/apme/pull/1",
      prNumber: 1,
    });
  });

  it("returns null for unrelated errors", () => {
    expect(parseExistingPrFromError("Branch already exists")).toBeNull();
  });
});

describe("extractExistingPrUrlFromError", () => {
  it("extracts GitHub PR URL from detail JSON", () => {
    expect(
      extractExistingPrUrlFromError(
        '{"detail":"PR already created for this activity: https://github.com/acme-scm/apme/pull/1"}',
      ),
    ).toBe("https://github.com/acme-scm/apme/pull/1");
  });

  it("returns null for unrelated errors", () => {
    expect(extractExistingPrUrlFromError("Branch already exists")).toBeNull();
  });
});

describe("apmeRemediationErrorTitle", () => {
  it("uses connection title for service failures", () => {
    expect(
      apmeRemediationErrorTitle("Failed to connect to APME: fetch failed"),
    ).toBe(APME_REMEDIATE_CONNECTION_TITLE);
  });

  it("defaults to no automated patches", () => {
    expect(apmeRemediationErrorTitle("nothing to apply")).toBe(
      "No automated patches",
    );
  });

  it("uses push title for remediate-activity errors", () => {
    expect(
      apmeRemediationErrorTitle(
        '{"detail":"Submit requires a remediate activity"}',
      ),
    ).toBe("Cannot push branch");
  });

  it("uses PR-exists title when a PR is already open", () => {
    expect(
      apmeRemediationErrorTitle(
        "PR already created for this activity: https://github.com/acme-scm/apme/pull/1",
      ),
    ).toBe("Pull request already exists");
  });
});
