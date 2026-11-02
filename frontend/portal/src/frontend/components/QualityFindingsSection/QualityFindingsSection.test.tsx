/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { TestApiProvider } from "@backstage/test-utils";
import {
  configApiRef,
  discoveryApiRef,
  fetchApiRef,
} from "@backstage/core-plugin-api";
import { ConfigReader } from "@backstage/config";
import type { Violation } from "../../../common/types";
import { apmeApiRef } from "../../api";
import { QualityFindingsSection } from "./QualityFindingsSection";

jest.mock("@apme/ui-workflow", () => ({
  AssessFindingsPanel: ({
    findings,
    resolveRuleHref,
    ruleHrefTarget,
  }: {
    findings: unknown[];
    resolveRuleHref?: (bareId: string) => string | undefined;
    ruleHrefTarget?: "_blank" | "_self";
  }) => (
    <div data-testid="assess-findings">
      {findings.length} findings
      {resolveRuleHref ? (
        <a href={resolveRuleHref("L001") ?? undefined} target={ruleHrefTarget}>
          L001
        </a>
      ) : null}
    </div>
  ),
}));

const violations: Violation[] = [
  {
    id: 1,
    rule_id: "L001",
    level: "medium",
    message: "use FQCN",
    file: "play.yml",
    line: 1,
    remediation_class: 1,
    validator_source: "native",
  },
];

describe("QualityFindingsSection", () => {
  const getRules = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    getRules.mockResolvedValue([{ id: "L001" }, { id: "M010" }]);
  });

  function renderSection() {
    return render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [apmeApiRef, { getRules }],
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [discoveryApiRef, { getBaseUrl: async () => "http://localhost" }],
            [fetchApiRef, { fetch: jest.fn() }],
          ]}
        >
          <ThemeProvider theme={createTheme()}>
            <QualityFindingsSection violations={violations} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );
  }

  it("loads the rules catalog and passes rule definition links", async () => {
    renderSection();
    expect(await screen.findByTestId("assess-findings")).toHaveTextContent(
      "1 findings",
    );
    expect(getRules).toHaveBeenCalled();
    const link = screen.getByRole("link", { name: "L001" });
    expect(link).toHaveAttribute(
      "href",
      "/self-service/repositories/quality-settings?rule=L001",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("omits rule links when the rules catalog cannot be loaded", async () => {
    getRules.mockRejectedValueOnce(new Error("offline"));
    renderSection();
    await waitFor(() => {
      expect(getRules).toHaveBeenCalled();
    });
    expect(
      screen.queryByRole("link", { name: "L001" }),
    ).not.toBeInTheDocument();
  });

  it("links findings when catalog rule IDs use the native: prefix", async () => {
    getRules.mockResolvedValueOnce([{ id: "native:L001" }]);
    renderSection();
    expect(await screen.findByRole("link", { name: "L001" })).toHaveAttribute(
      "href",
      "/self-service/repositories/quality-settings?rule=L001",
    );
  });
});
