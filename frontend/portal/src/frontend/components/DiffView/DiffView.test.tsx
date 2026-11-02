import "@testing-library/jest-dom";
import { render, screen, cleanup } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@material-ui/core/styles";
import { DiffView } from "./DiffView";

describe("DiffView", () => {
  it("renders nothing without content", () => {
    const { container } = render(<DiffView />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders removed and added lines with their source line numbers", () => {
    const { container } = render(
      <DiffView
        before={"old\nsame"}
        after={"new\nsame"}
        title="Changes"
        fillHeight
      />,
    );
    expect(screen.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.getByText("new")).toBeInTheDocument();
    expect(screen.getByText("same")).toBeInTheDocument();
    expect(container.textContent).toContain("−old");
    expect(container.textContent).toContain("+new");
  });

  it.each([
    { before: "deleted", text: "deleted", prefix: "−" },
    { after: "created", text: "created", prefix: "+" },
  ])("handles a one-sided change: $text", (props) => {
    const { container } = render(<DiffView {...props} />);
    expect(screen.getByText(props.text)).toBeInTheDocument();
    expect(container.textContent).toContain(`${props.prefix}${props.text}`);
  });

  it("prefers supplied unified hunks over before and after", () => {
    const diff =
      "--- a/tasks.yml\n+++ b/tasks.yml\n@@ -7,2 +8,2 @@\n-removed\n+added\n context\n";
    render(
      <DiffView before="ignored-before" after="ignored-after" diff={diff} />,
    );
    expect(screen.getByText("removed")).toBeInTheDocument();
    expect(screen.getByText("added")).toBeInTheDocument();
    expect(screen.getByText("context")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.queryByText("ignored-before")).not.toBeInTheDocument();
  });

  it("handles hunk text without offsets and changes before a hunk header", () => {
    render(<DiffView diff={"-before\n+after\n@@ metadata @@\n unchanged"} />);
    expect(screen.getByText("before")).toBeInTheDocument();
    expect(screen.getByText("after")).toBeInTheDocument();
    expect(screen.getByText("unchanged")).toBeInTheDocument();
  });

  it("renders file headers and context lines without a leading space", () => {
    render(
      <DiffView
        diff={"--- a/tasks.yml\n+++ b/tasks.yml\nno-leading-space-context"}
      />,
    );
    expect(screen.getByText("--- a/tasks.yml")).toBeInTheDocument();
    expect(screen.getByText("+++ b/tasks.yml")).toBeInTheDocument();
    expect(screen.getByText("no-leading-space-context")).toBeInTheDocument();
  });

  it("parses hunk headers with comma ranges and assigns line numbers", () => {
    render(<DiffView diff={"@@ -12,2 +14,2 @@\n removed\n+added\n context"} />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("removed")).toBeInTheDocument();
    expect(screen.getByText("added")).toBeInTheDocument();
    expect(screen.getByText("context")).toBeInTheDocument();
  });

  it("skips trailing blank lines inside a hunk", () => {
    const { container } = render(<DiffView diff={"-only\n+\n"} />);
    const { container: withoutTrailingNewline } = render(
      <DiffView diff={"-only\n+"} />,
    );
    expect(screen.getAllByText("only")).toHaveLength(2);
    expect(container.textContent).toBe(withoutTrailingNewline.textContent);
    expect(container.querySelectorAll("div").length).toBe(
      withoutTrailingNewline.querySelectorAll("div").length,
    );
  });

  it("assigns line numbers when hunk numbers are positive", () => {
    const { container } = render(
      <DiffView diff={"@@ -5,3 +10,3 @@\n-removed\n+added\n ctx"} />,
    );
    // removed line should show oldNum 5
    expect(screen.getByText("5")).toBeInTheDocument();
    // added line should show newNum 10
    expect(screen.getByText("10")).toBeInTheDocument();
    // context should show both 6 and 11
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("11")).toBeInTheDocument();
    expect(container.textContent).toContain("removed");
    expect(container.textContent).toContain("added");
  });

  it("omits line numbers when diff lines appear before any hunk header", () => {
    const { container } = render(<DiffView diff={"-old\n+new\n plain"} />);
    // Lines before any @@ header have state {0,0} → no numeric line numbers
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.getByText("new")).toBeInTheDocument();
    expect(screen.getByText("plain")).toBeInTheDocument();
    // No digits should appear in the line-number cells
    const lineNumbers = container.querySelectorAll("span");
    const numericSpans = Array.from(lineNumbers).filter((el) =>
      /^\d+$/.test(el.textContent ?? ""),
    );
    expect(numericSpans).toHaveLength(0);
  });

  it("renders labeled side-by-side panels and tolerates a missing side", () => {
    render(
      <DiffView
        before="previous"
        layout="sideBySide"
        afterLabel="Proposed"
        title="Patch"
      />,
    );
    expect(screen.getByText("Before")).toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByText("previous")).toBeInTheDocument();
    cleanup();
    render(<DiffView after="replacement" layout="sideBySide" />);
    expect(screen.getByText("After (proposed)")).toBeInTheDocument();
    expect(screen.getByText("replacement")).toBeInTheDocument();
  });

  it("still renders a unified hunk when side-by-side mode is requested", () => {
    render(<DiffView diff={"@@ -1 +1 @@\n-old\n+new"} layout="sideBySide" />);
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.queryByText("After (proposed)")).not.toBeInTheDocument();
  });

  it("avoids quadratic diff computation for oversized content", () => {
    render(
      <DiffView
        before={Array.from({ length: 2001 }, (_, n) => `old-${n}`).join("\n")}
        after="new-last"
      />,
    );
    expect(screen.getByText("old-2000")).toBeInTheDocument();
    expect(screen.getByText("new-last")).toBeInTheDocument();
  });

  it("supports dark-theme presentation without changing diff content", () => {
    render(
      <ThemeProvider theme={createTheme({ palette: { type: "dark" } })}>
        <DiffView before="old" after="new" />
      </ThemeProvider>,
    );
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.getByText("new")).toBeInTheDocument();
  });
});
