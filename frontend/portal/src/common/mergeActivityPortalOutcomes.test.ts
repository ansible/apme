/*
 * Copyright Red Hat
 */

import { mergeActivityPortalOutcomes } from "./mergeActivityPortalOutcomes";
import type { Activity } from "./types";

describe("mergeActivityPortalOutcomes", () => {
  const baseActivity: Activity = {
    scan_id: "scan-1",
    session_id: "sess-1",
    project_path: "demo",
    source: "manual",
    created_at: "2026-07-18T00:00:00Z",
    scan_type: "remediate",
    total_violations: 10,
    fixable: 5,
    ai_candidate: 0,
    ai_proposed: 0,
    ai_declined: 0,
    ai_accepted: 0,
    manual_review: 0,
    remediated_count: 5,
  };

  it("returns activities unchanged when no outcomes", () => {
    const activities = [baseActivity];
    expect(mergeActivityPortalOutcomes(activities)).toEqual(activities);
    expect(mergeActivityPortalOutcomes(activities, {})).toEqual(activities);
  });

  it("merges stored branch_name and pr_url when gateway omits them", () => {
    const merged = mergeActivityPortalOutcomes([baseActivity], {
      "scan-1": {
        branch_name: "apme/remediate-abc",
        pr_url: null,
      },
    });

    expect(merged[0].branch_name).toBe("apme/remediate-abc");
    expect(merged[0].pr_url).toBeNull();
  });

  it("prefers gateway fields when present", () => {
    const withGateway = [
      {
        ...baseActivity,
        branch_name: "gateway-branch",
        pr_url: "https://github.com/org/repo/pull/1",
      },
    ];
    const merged = mergeActivityPortalOutcomes(withGateway, {
      "scan-1": {
        branch_name: "stored-branch",
        pr_url: "https://github.com/org/repo/pull/99",
      },
    });

    expect(merged[0].branch_name).toBe("gateway-branch");
    expect(merged[0].pr_url).toBe("https://github.com/org/repo/pull/1");
  });

  it("fills pr_url from store when gateway returns null", () => {
    const withNullPr = [{ ...baseActivity, pr_url: null }];
    const merged = mergeActivityPortalOutcomes(withNullPr, {
      "scan-1": {
        branch_name: "apme/remediate-abc",
        pr_url: "https://github.com/org/repo/pull/2",
      },
    });

    expect(merged[0].pr_url).toBe("https://github.com/org/repo/pull/2");
  });

  it("merges stored commit_sha when gateway omits it", () => {
    const merged = mergeActivityPortalOutcomes([baseActivity], {
      "scan-1": {
        branch_name: "apme/remediate-abc",
        commit_sha: "abc12345",
      },
    });

    expect(merged[0].commit_sha).toBe("abc12345");
  });
});
