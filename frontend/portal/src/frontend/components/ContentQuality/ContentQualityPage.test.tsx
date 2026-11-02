import "@testing-library/jest-dom";
import { screen } from "@testing-library/react";
import { mockApis, renderTestApp } from "@backstage/frontend-test-utils";
import apmePlugin from "../../alpha";

// A separate test module isolates the legacy permission hook's SWR cache from
// allow-path tests while exercising the real permission boundary through NFS.
describe("Content Quality route authorization", () => {
  it("does not redirect when repository-view permission is denied", async () => {
    await renderTestApp({
      features: [apmePlugin],
      config: {
        backend: { baseUrl: "http://localhost:7007" },
        permission: { enabled: true },
        ansible: { apme: { enabled: true, mockMode: true } },
      },
      initialRouteEntries: ["/self-service/content-quality"],
      apis: [mockApis.permission({ authorize: "DENY" })],
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to view Content Quality.",
    );
    expect(
      screen.queryByText(/Content Quality is unavailable/),
    ).not.toBeInTheDocument();
  });
});
