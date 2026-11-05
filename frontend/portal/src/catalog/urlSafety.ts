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
import { lookup } from "node:dns/promises";
import { isIP } from "node:net";

const PROVIDER_ID_RE = /^[A-Za-z0-9_-]+$/;

/** Reject unsafe provider ids before proxying to Abbenay. */
export function assertSafeAbbenayProviderId(providerId: string): void {
  if (!PROVIDER_ID_RE.test(providerId)) {
    throw new InputError(
      "Provider id must match [A-Za-z0-9_-]+ (no spaces, newlines, or shell metacharacters)",
    );
  }
}

function ipv4IsUnsafe(address: string): boolean {
  const octets = address.split(".").map(Number);
  const [a, b, c] = octets;
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0 && c === 0) ||
    (a === 192 && b === 168) ||
    (a === 192 && b === 0 && c === 2) ||
    (a === 192 && b === 88 && c === 99) ||
    (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) ||
    (a === 203 && b === 0 && c === 113) ||
    a >= 224
  );
}

function ipv6Value(address: string): bigint {
  let value = address.toLowerCase();
  if (value.includes(".")) {
    const separator = value.lastIndexOf(":");
    const ipv4 = value
      .slice(separator + 1)
      .split(".")
      .map(Number);
    const high = ((ipv4[0] << 8) | ipv4[1]).toString(16);
    const low = ((ipv4[2] << 8) | ipv4[3]).toString(16);
    value = `${value.slice(0, separator)}:${high}:${low}`;
  }
  const halves = value.split("::");
  const left = halves[0] ? halves[0].split(":") : [];
  const right = halves.length > 1 && halves[1] ? halves[1].split(":") : [];
  const zeros = Array(Math.max(0, 8 - left.length - right.length)).fill("0");
  return [...left, ...zeros, ...right].reduce(
    (result, word) =>
      (result << 16n) | BigInt(Number.parseInt(word || "0", 16)),
    0n,
  );
}

function hasIpv6Prefix(address: bigint, prefix: bigint, bits: number): boolean {
  return address >> BigInt(128 - bits) === prefix;
}

function addressIsUnsafe(address: string): boolean {
  const normalized = address.replace(/^\[|\]$/g, "").toLowerCase();
  const family = isIP(normalized);
  if (family === 4) {
    return ipv4IsUnsafe(normalized);
  }
  if (family !== 6) {
    return true;
  }

  const value = ipv6Value(normalized);
  const top96 = value >> 32n;
  if (top96 === 0xffffn || (top96 === 0n && value > 1n)) {
    const embeddedIpv4 = Number(value & 0xffffffffn);
    const ipv4 = [
      (embeddedIpv4 >>> 24) & 255,
      (embeddedIpv4 >>> 16) & 255,
      (embeddedIpv4 >>> 8) & 255,
      embeddedIpv4 & 255,
    ].join(".");
    return ipv4IsUnsafe(ipv4);
  }

  return (
    value <= 1n ||
    hasIpv6Prefix(value, 0x7en, 7) || // Unique local (fc00::/7)
    hasIpv6Prefix(value, 0x3fan, 10) || // Link local (fe80::/10)
    hasIpv6Prefix(value, 0xffn, 8) || // Multicast
    hasIpv6Prefix(value, 0x20010db8n, 32) || // Documentation
    hasIpv6Prefix(value, 0x100080n, 23) || // Special-purpose 2001::/23
    hasIpv6Prefix(value, 0x2002n, 16) || // 6to4 embeds an IPv4 destination
    hasIpv6Prefix(value, 0x64ff9bn, 96) || // Well-known NAT64 prefix
    hasIpv6Prefix(value, 0x64ff9b000001n, 48) // Local-use NAT64 prefix
  );
}

/** Allow https URLs only; reject private/link-local/metadata destinations by default. */
export async function assertSafeHttpUrl(
  raw: string,
  fieldName: string,
  options?: { allowHttp?: boolean; allowPrivateHosts?: boolean },
): Promise<URL> {
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
  const host = parsed.hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.+$/, "");
  if (
    host === "localhost" ||
    host === "metadata.google.internal" ||
    host.endsWith(".local")
  ) {
    throw new InputError(
      `${fieldName} must not target private, link-local, or metadata hosts`,
    );
  }
  if (isIP(host) !== 0) {
    if (addressIsUnsafe(host)) {
      throw new InputError(
        `${fieldName} must not target private, link-local, or metadata hosts`,
      );
    }
    return parsed;
  }

  let addresses: Array<{ address: string; family: number }>;
  try {
    addresses = await lookup(host, { all: true, verbatim: true });
  } catch {
    throw new InputError(`${fieldName} host could not be resolved safely`);
  }
  if (
    addresses.length === 0 ||
    addresses.some(({ address }) => addressIsUnsafe(address))
  ) {
    throw new InputError(
      `${fieldName} must not resolve to private, link-local, or metadata hosts`,
    );
  }
  return parsed;
}
