/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { Violation } from "../../common/types";
import type { ApmeApi } from "../api/ApmeApi";

/** Gateway page size; fetch all pages when a project exceeds this. */
export const VIOLATIONS_PAGE_SIZE = 500;

/** Rule-based fix (tier 1) rows first, then stable by violation id. */
export function sortRuleBasedFixFirst(violations: Violation[]): Violation[] {
  return [...violations].sort((a, b) => {
    const aAuto = a.remediation_class === 1 ? 0 : 1;
    const bAuto = b.remediation_class === 1 ? 0 : 1;
    if (aAuto !== bAuto) return aAuto - bAuto;
    return a.id - b.id;
  });
}

/**
 * Load every violation page from the gateway, then sort rule-based fix rows first.
 */
export async function fetchAllProjectViolations(
  apmeApi: Pick<ApmeApi, "getViolations">,
  projectId: string,
  totalHint?: number | null,
): Promise<Violation[]> {
  const all: Violation[] = [];
  let offset = 0;
  let hasMore = true;

  while (hasMore) {
    // prettier-ignore
    const page = await apmeApi.getViolations(projectId, { limit: VIOLATIONS_PAGE_SIZE, offset }); // NOSONAR - paginated gateway fetch must be sequential
    all.push(...page);

    if (page.length < VIOLATIONS_PAGE_SIZE) {
      hasMore = false;
    } else if (
      totalHint !== undefined &&
      totalHint !== null &&
      all.length >= totalHint
    ) {
      hasMore = false;
    } else {
      offset += VIOLATIONS_PAGE_SIZE;
    }
  }

  return sortRuleBasedFixFirst(all);
}
