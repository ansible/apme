/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import {
  ApmeAddRepositoryHeaderAction,
  APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH,
} from "./ApmeAddRepositoryHeaderAction";

describe("ApmeAddRepositoryHeaderAction", () => {
  it("links to the Self-service register template", () => {
    render(
      <MemoryRouter>
        <ApmeAddRepositoryHeaderAction />
      </MemoryRouter>,
    );

    // LinkButton is an <a> with MUI button styles (role=button + href).
    expect(
      screen.getByRole("button", { name: /add repository/i }),
    ).toHaveAttribute("href", APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH);
  });
});
