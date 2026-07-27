import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  getPersistedSession,
  type PersistedSession,
} from '@apme/ui-workflow';

const STORAGE_KEY = "apme_active_session";

describe("getPersistedSession", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null when nothing is stored", () => {
    expect(getPersistedSession()).toBeNull();
  });

  it("returns a valid persisted session", () => {
    const data: PersistedSession = {
      sessionId: "sess-1",
      scanId: "scan-1",
      timestamp: Date.now(),
      ttlSeconds: 1800,
    };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));

    const result = getPersistedSession();
    expect(result).toEqual(data);
  });

  it("returns null and clears storage when TTL is exceeded", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-13T12:00:00Z"));

    const data: PersistedSession = {
      sessionId: "sess-1",
      scanId: "scan-1",
      timestamp: Date.now(),
      ttlSeconds: 600,
    };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));

    vi.setSystemTime(new Date("2026-04-13T12:11:00Z"));

    expect(getPersistedSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns session when within TTL", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-13T12:00:00Z"));

    const data: PersistedSession = {
      sessionId: "sess-1",
      scanId: "scan-1",
      timestamp: Date.now(),
      ttlSeconds: 1800,
    };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));

    vi.setSystemTime(new Date("2026-04-13T12:29:00Z"));

    expect(getPersistedSession()).toEqual(data);
  });

  it("respects server-provided TTL instead of hardcoded default", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-13T12:00:00Z"));

    const data: PersistedSession = {
      sessionId: "sess-1",
      scanId: "scan-1",
      timestamp: Date.now(),
      ttlSeconds: 300,
    };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));

    vi.setSystemTime(new Date("2026-04-13T12:06:00Z"));

    expect(getPersistedSession()).toBeNull();
  });

  it("returns null and clears storage for corrupt JSON", () => {
    sessionStorage.setItem(STORAGE_KEY, "not-json{{{");

    expect(getPersistedSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns null and clears storage when fields are missing", () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ sessionId: "sess-1" }),
    );

    expect(getPersistedSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("returns null and clears storage when fields have wrong types", () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessionId: 123,
        scanId: "scan-1",
        timestamp: "not-a-number",
        ttlSeconds: 1800,
      }),
    );

    expect(getPersistedSession()).toBeNull();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
