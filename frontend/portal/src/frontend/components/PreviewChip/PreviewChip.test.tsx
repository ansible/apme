/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import type { ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { TestApiProvider } from "@backstage/test-utils";
import { configApiRef } from "@backstage/core-plugin-api";
import { DEFAULT_APME_FEEDBACK_FORM_URL } from "../../constants/previewFeedback";
import {
  PreviewChip,
  PreviewFeedbackLink,
  PreviewLabelRow,
  PreviewNotice,
  usePreviewFeedbackUrl,
} from "./PreviewChip";
import { getPreviewSurfaceTokens } from "../../../common/severity";

const theme = createTheme();
const darkTheme = createTheme({ palette: { type: "dark" } });

function renderWithConfig(
  ui: ReactElement,
  config: Record<string, string | boolean> = { "ansible.apme.enabled": true },
  themeOverride = theme,
) {
  const configApi = {
    getOptionalString: (key: string) => {
      const value = config[key];
      return typeof value === "string" ? value : undefined;
    },
    getOptionalBoolean: (key: string) => {
      const value = config[key];
      return typeof value === "boolean" ? value : undefined;
    },
  };
  return render(
    <TestApiProvider apis={[[configApiRef, configApi]]}>
      <ThemeProvider theme={themeOverride}>{ui}</ThemeProvider>
    </TestApiProvider>,
  );
}

function FeedbackUrlProbe() {
  const url = usePreviewFeedbackUrl();
  return <span data-testid="feedback-url">{url ?? "none"}</span>;
}

describe("usePreviewFeedbackUrl", () => {
  it("returns default URL when config key is omitted", () => {
    renderWithConfig(<FeedbackUrlProbe />);
    expect(screen.getByTestId("feedback-url")).toHaveTextContent(
      DEFAULT_APME_FEEDBACK_FORM_URL,
    );
  });

  it("returns override when config is set", () => {
    renderWithConfig(<FeedbackUrlProbe />, {
      "ansible.apme.enabled": true,
      "ansible.apme.feedbackFormUrl": "https://example.com/form",
    });
    expect(screen.getByTestId("feedback-url")).toHaveTextContent(
      "https://example.com/form",
    );
  });

  it("returns none when feedbackFormUrl is empty", () => {
    renderWithConfig(<FeedbackUrlProbe />, {
      "ansible.apme.enabled": true,
      "ansible.apme.feedbackFormUrl": "",
    });
    expect(screen.getByTestId("feedback-url")).toHaveTextContent("none");
  });

  it("returns none when apme is disabled", () => {
    renderWithConfig(<FeedbackUrlProbe />, {
      "ansible.apme.enabled": false,
    });
    expect(screen.getByTestId("feedback-url")).toHaveTextContent("none");
  });
});

describe("PreviewChip", () => {
  it("applies light-mode prominent tokens", () => {
    renderWithConfig(<PreviewChip variant="prominent" />);
    const chip = screen.getByTestId("preview-chip");
    const tokens = getPreviewSurfaceTokens("light");
    expect(chip).toHaveStyle({
      backgroundColor: tokens.prominentBackground,
      color: tokens.prominentText,
    });
  });

  it("applies dark-mode prominent tokens", () => {
    renderWithConfig(<PreviewChip variant="prominent" />, undefined, darkTheme);
    const chip = screen.getByTestId("preview-chip");
    const tokens = getPreviewSurfaceTokens("dark");
    expect(chip).toHaveStyle({
      backgroundColor: tokens.prominentBackground,
      color: tokens.prominentText,
    });
  });

  it("applies outlined tokens for default variant", () => {
    renderWithConfig(<PreviewChip />);
    const chip = screen.getByTestId("preview-chip");
    const tokens = getPreviewSurfaceTokens("light");
    expect(chip).toHaveStyle({
      color: tokens.outlinedText,
      borderColor: tokens.outlinedBorder,
    });
  });
});

describe("PreviewFeedbackLink", () => {
  it("shows Share your feedback with default href", () => {
    renderWithConfig(<PreviewFeedbackLink />);
    const link = screen.getByRole("link", { name: /Share your feedback/i });
    expect(link).toHaveAttribute("href", DEFAULT_APME_FEEDBACK_FORM_URL);
  });

  it("hides when feedbackFormUrl is empty", () => {
    renderWithConfig(<PreviewFeedbackLink />, {
      "ansible.apme.enabled": true,
      "ansible.apme.feedbackFormUrl": "",
    });
    expect(
      screen.queryByTestId("preview-feedback-link"),
    ).not.toBeInTheDocument();
  });
});

describe("PreviewLabelRow", () => {
  it("renders chip and feedback link together", () => {
    renderWithConfig(<PreviewLabelRow />);
    expect(screen.getByTestId("preview-chip")).toBeInTheDocument();
    expect(screen.getByTestId("preview-feedback-link")).toBeInTheDocument();
  });
});

describe("PreviewNotice", () => {
  it("renders prominent chip with feedback link", () => {
    renderWithConfig(<PreviewNotice />);
    expect(screen.getByTestId("preview-chip")).toHaveTextContent(
      "Early access preview",
    );
    expect(
      screen.getByRole("link", { name: /Share your feedback/i }),
    ).toBeInTheDocument();
  });
});
