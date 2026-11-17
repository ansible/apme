import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BUIProvider } from "@backstage/ui";
import { MemoryRouter } from "react-router-dom";
import { ApmeApiProvider } from "@apme/ui-workflow/headless";
import { FeedbackModal } from "../../packages/ui-workflow/src/shared/FeedbackModal";

it("submits selected feedback through the host adapter and permits retry after failure", async () => {
  const user = userEvent.setup();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ detail: "Feedback unavailable" }, { status: 503 }),
    )
    .mockResolvedValueOnce(
      Response.json({ issue_url: "https://github.com/acme/repo/issues/42" }),
    );
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeApiProvider adapter={{ apiBase: "/host/apme", fetch }}>
          <FeedbackModal
            isOpen
            onClose={onClose}
            prefill={{ rule_id: "L050", scan_id: "scan-1" }}
          />
        </ApmeApiProvider>
      </BUIProvider>
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: /Issue type/ }));
  await user.click(await screen.findByRole("option", { name: /Rule Misfire/ }));
  await user.type(
    screen.getByRole("textbox", { name: "Comment" }),
    "Unexpected finding",
  );
  await user.click(screen.getByRole("button", { name: "Submit" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Feedback unavailable",
  );
  expect(screen.getByRole("textbox", { name: "Comment" })).toHaveValue(
    "Unexpected finding",
  );
  await user.click(screen.getByRole("button", { name: "Submit" }));
  const link = await screen.findByRole("link", {
    name: "https://github.com/acme/repo/issues/42",
  });
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(fetch).toHaveBeenLastCalledWith(
    "/host/apme/feedback",
    expect.objectContaining({
      method: "POST",
      body: expect.any(String),
    }),
  );
  const body = JSON.parse(fetch.mock.calls[1]![1].body);
  expect(body).toMatchObject({
    type: "rule_misfire",
    rule_id: "L050",
    scan_id: "scan-1",
    user_comment: "Unexpected finding",
  });
  await user.click(screen.getAllByRole("button", { name: "Close" })[1]!);
  expect(onClose).toHaveBeenCalledOnce();
});

it("dismisses the BUI dialog with Escape without submitting feedback", async () => {
  const user = userEvent.setup();
  const fetch = vi.fn();
  const onClose = vi.fn();
  render(
    <BUIProvider>
      <ApmeApiProvider adapter={{ fetch }}>
        <FeedbackModal isOpen onClose={onClose} />
      </ApmeApiProvider>
    </BUIProvider>,
  );
  await screen.findByRole("dialog", { name: "Report Issue" });
  await user.keyboard("{Escape}");
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(fetch).not.toHaveBeenCalled();
});
