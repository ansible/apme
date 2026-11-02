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

import { InputError } from "@backstage/errors";

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;

/** Reject unsafe provider ids before proxying to Abbenay. */
export function assertSafeAbbenayProviderId(providerId: string): void {
  if (!PROVIDER_ID_RE.test(providerId)) {
    throw new InputError(
      "Provider id must match [A-Za-z0-9_-]+ (no spaces, newlines, or shell metacharacters)",
    );
  }
}

/** Allow https URLs only; reject private/link-local/metadata hosts by default. */
export function assertSafeHttpUrl(
  raw: string,
  fieldName: string,
  options?: { allowHttp?: boolean; allowPrivateHosts?: boolean },
): URL {
  let parsed: URL;
  try {
    parsed = new URL(raw.trim());
  } catch {
    throw new InputError(`${fieldName} must be a valid URL`);
  }
  const allowHttp = options?.allowHttp === true;
  if (
    parsed.protocol !== "https:" &&
    !(allowHttp && parsed.protocol === "http:")
  ) {
    throw new InputError(
      `${fieldName} must use https${allowHttp ? " or http" : ""}`,
    );
  }
  if (options?.allowPrivateHosts) {
    return parsed;
  }
  const host = parsed.hostname.toLowerCase();
  if (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host === "metadata.google.internal" ||
    host.endsWith(".local") ||
    host.startsWith("169.254.") ||
    host.startsWith("10.") ||
    /^172\.(1[6-9]|2\d|3[0-1])\./.test(host) ||
    host.startsWith("192.168.")
  ) {
    throw new InputError(
      `${fieldName} must not target private, link-local, or metadata hosts`,
    );
  }
  return parsed;
}
