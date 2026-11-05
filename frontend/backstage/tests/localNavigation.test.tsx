import type { AnchorHTMLAttributes, ComponentType, ReactNode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import type { NavContentComponentProps } from "@backstage/plugin-app-react";
import { LocalNavigation } from "../../../containers/rhdh/plugin/src/LocalNavigation";

type SidebarItemProps = Omit<
  AnchorHTMLAttributes<HTMLAnchorElement>,
  "href"
> & {
  to: string;
  text: string;
  icon?: ComponentType;
  end?: boolean;
};

vi.mock("@backstage/core-components", async () => {
  const React = await import("react");
  return {
    Sidebar: ({ children }: { children: ReactNode }) => <nav>{children}</nav>,
    SidebarItem: ({
      to,
      text,
      icon: Icon,
      end: _end,
      ...props
    }: SidebarItemProps) => (
      <a href={to} {...props}>
        {Icon && <Icon />}
        {text}
      </a>
    ),
    useSidebarOpenState: () => {
      const [isOpen, setOpen] = React.useState(true);
      return { isOpen, setOpen };
    },
  };
});

vi.mock("../../../containers/rhdh/plugin/src/LocalThemeControl", () => ({
  LocalThemeControl: () => null,
}));

it("keeps the APME brand link navigable and exposes a separate section toggle", () => {
  const navItems = {
    rest: () => [
      {
        href: "/",
        title: "Projects",
        icon: <span aria-hidden="true" />,
        node: { spec: { id: "page:projects", plugin: { id: "apme" } } },
      },
      {
        href: "/catalog",
        title: "Catalog",
        icon: <span aria-hidden="true" />,
        node: { spec: { id: "page:catalog", plugin: { id: "catalog" } } },
      },
    ],
  } as unknown as NavContentComponentProps["navItems"];

  render(<LocalNavigation navItems={navItems} items={[]} />);

  const brandLink = screen.getByRole("link", { name: "APME" });
  expect(brandLink.getAttribute("href")).toBe("/");
  expect(fireEvent.click(brandLink)).toBe(true);

  const sections = document.getElementById("apme-local-sections");
  expect(sections?.hidden).toBe(false);
  const collapse = screen.getByRole("button", {
    name: "Collapse APME sections",
  });
  expect(collapse.getAttribute("aria-controls")).toBe("apme-local-sections");
  fireEvent.click(collapse);

  expect(sections?.hidden).toBe(true);
  const expand = screen.getByRole("button", {
    name: "Expand APME sections",
  });
  fireEvent.click(expand);
  expect(sections?.hidden).toBe(false);
});
