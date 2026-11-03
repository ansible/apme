/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { Violation } from "../../common/types";
import {
  fetchAllProjectViolations,
  sortRuleBasedFixFirst,
  VIOLATIONS_PAGE_SIZE,
} from "./fetchAllProjectViolations";

function violation(id: number, remediationClass: 1 | 2 | 3): Violation {
  return {
    id,
    rule_id: "R001",
    file: "playbook.yml",
    line: 1,
    level: "warning",
    message: "test",
    remediation_class: remediationClass,
  } as Violation;
}

describe("sortRuleBasedFixFirst", () => {
  it("places rule-based fix (tier 1) violations before others", () => {
    const sorted = sortRuleBasedFixFirst([
      violation(3, 2),
      violation(1, 1),
      violation(2, 3),
    ]);
    expect(sorted.map((v) => v.id)).toEqual([1, 2, 3]);
  });
});

describe("fetchAllProjectViolations", () => {
  it("pages until a short final page and sorts rule-based fix first", async () => {
    const pages = Array.from({ length: VIOLATIONS_PAGE_SIZE + 2 }, (_, i) =>
      violation(i + 1, i === VIOLATIONS_PAGE_SIZE ? 1 : 2),
    );
    const getViolations = jest.fn(
      async (
        _projectId: string,
        options?: { limit?: number; offset?: number },
      ) => {
        const offset = options?.offset ?? 0;
        const limit = options?.limit ?? pages.length;
        return pages.slice(offset, offset + limit);
      },
    );

    const result = await fetchAllProjectViolations(
      { getViolations },
      "project-1",
      pages.length,
    );

    expect(getViolations).toHaveBeenCalledTimes(2);
    expect(result).toHaveLength(pages.length);
    expect(result[0].remediation_class).toBe(1);
  });
});
