/*
 * Copyright Red Hat
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import type { ReactNode } from "react";
import type { Violation } from "../../common/types";
import { apmeApiRef } from "../api";
import {
  isDuplicateSuppressionError,
  pickSuppressionForViolation,
  useViolationAcknowledge,
} from "./useViolationAcknowledge";

describe("useViolationAcknowledge", () => {
  const createSuppression = jest.fn();
  const deleteSuppression = jest.fn();
  const getSuppressions = jest.fn();

  const wrapper = ({ children }: { children: ReactNode }) => (
    <TestApiProvider
      apis={[
        [apmeApiRef, { createSuppression, deleteSuppression, getSuppressions }],
      ]}
    >
      {children}
    </TestApiProvider>
  );

  beforeEach(() => {
    jest.clearAllMocks();
    createSuppression.mockResolvedValue({ id: 1 });
    deleteSuppression.mockResolvedValue(undefined);
    getSuppressions.mockResolvedValue([]);
  });

  const violation: Violation = {
    id: 42,
    rule_id: "L001",
    level: "medium",
    message: "msg",
    file: "a.yml",
    line: 1,
    remediation_class: 3,
    validator_source: "native",
    original_yaml: "debug: msg=hi",
  };

  it("creates a project-scoped suppression and tracks id", async () => {
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.acknowledge(violation);
    });

    expect(createSuppression).toHaveBeenCalledWith({
      rule_id: "L001",
      original_yaml: "debug: msg=hi",
      fingerprint_mode: "full",
      scope: "project:proj-1",
      reason: "Acknowledged via Quality triage",
    });
    expect(result.current.acknowledgedIds.has(42)).toBe(true);
  });

  it("treats ApmeApiClient conflict errors as already acknowledged", async () => {
    createSuppression.mockRejectedValueOnce(
      new Error("APME API conflict: already exists"),
    );
    getSuppressions.mockResolvedValueOnce([
      {
        id: 9,
        fingerprint_hash: "abc",
        fingerprint_mode: "full",
        rule_id: "L001",
        scope: "project:proj-1",
        reason: "x",
        created_by: "u",
        created_at: "t",
      },
    ]);
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.acknowledge(violation);
    });

    await waitFor(() => {
      expect(result.current.acknowledgedIds.has(42)).toBe(true);
    });
    expect(result.current.ackError).toBeNull();
  });

  it("sets ackError on non-409 failures", async () => {
    createSuppression.mockRejectedValueOnce(new Error("gateway down"));
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.acknowledge(violation);
    });

    expect(result.current.acknowledgedIds.has(42)).toBe(false);
    expect(result.current.ackError).toContain("gateway down");
  });

  it("unacknowledges using cached suppression id", async () => {
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.acknowledge(violation);
    });
    await act(async () => {
      await result.current.unacknowledge(violation);
    });

    expect(deleteSuppression).toHaveBeenCalledWith(1);
    expect(result.current.acknowledgedIds.has(42)).toBe(false);
  });

  it("does not clear UI when multiple suppressions share rule_id", async () => {
    getSuppressions.mockResolvedValueOnce([
      {
        id: 1,
        fingerprint_hash: "a",
        fingerprint_mode: "full",
        rule_id: "L001",
        scope: "project:proj-1",
        reason: "x",
        created_by: "u",
        created_at: "t",
      },
      {
        id: 2,
        fingerprint_hash: "b",
        fingerprint_mode: "full",
        rule_id: "L001",
        scope: "project:proj-1",
        reason: "y",
        created_by: "u",
        created_at: "t",
      },
    ]);
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.unacknowledge(violation);
    });

    expect(deleteSuppression).not.toHaveBeenCalled();
    expect(result.current.ackError).toMatch(/multiple suppressions/i);
  });

  it("does not clear UI when no suppression is found", async () => {
    getSuppressions.mockResolvedValueOnce([]);
    const { result } = renderHook(() => useViolationAcknowledge("proj-1"), {
      wrapper,
    });

    await act(async () => {
      await result.current.unacknowledge(violation);
    });

    expect(deleteSuppression).not.toHaveBeenCalled();
    expect(result.current.ackError).toMatch(/No matching suppression/i);
  });
});

describe("isDuplicateSuppressionError", () => {
  it("matches status 409 and APME API conflict messages", () => {
    expect(isDuplicateSuppressionError({ status: 409 })).toBe(true);
    expect(isDuplicateSuppressionError(new Error("APME API conflict: x"))).toBe(
      true,
    );
    expect(isDuplicateSuppressionError(new Error("gateway down"))).toBe(false);
  });
});

describe("pickSuppressionForViolation", () => {
  it("returns only when exactly one rule_id match exists", () => {
    const list = [
      {
        id: 1,
        fingerprint_hash: "a",
        fingerprint_mode: "full",
        rule_id: "L001",
        scope: "p",
        reason: "",
        created_by: "",
        created_at: "",
      },
      {
        id: 2,
        fingerprint_hash: "b",
        fingerprint_mode: "full",
        rule_id: "L002",
        scope: "p",
        reason: "",
        created_by: "",
        created_at: "",
      },
    ];
    expect(
      pickSuppressionForViolation(list, {
        id: 1,
        rule_id: "L001",
        level: "low",
        message: "",
        file: "",
        line: 1,
        remediation_class: 3,
        validator_source: "native",
      })?.id,
    ).toBe(1);
    expect(
      pickSuppressionForViolation(
        [
          ...list,
          {
            id: 3,
            fingerprint_hash: "c",
            fingerprint_mode: "full",
            rule_id: "L001",
            scope: "p",
            reason: "",
            created_by: "",
            created_at: "",
          },
        ],
        {
          id: 1,
          rule_id: "L001",
          level: "low",
          message: "",
          file: "",
          line: 1,
          remediation_class: 3,
          validator_source: "native",
        },
      ),
    ).toBeUndefined();
  });
});
