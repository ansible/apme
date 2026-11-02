/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  Sidebar,
  SidebarItem,
  useSidebarOpenState,
} from "@backstage/core-components";
import type { NavContentComponentProps } from "@backstage/plugin-app-react";
import { Link } from "react-router-dom";

const productIcon = new URL(
  "../../../../frontend/public/apme.svg",
  import.meta.url,
).href;

function Brand() {
  const { isOpen } = useSidebarOpenState();
  return (
    <Link
      to="/"
      aria-label="APME home"
      className="apme-local-brand"
      data-expanded={isOpen}
    >
      <img src={productIcon} alt="" width="32" height="32" />
      {isOpen && <span>APME</span>}
    </Link>
  );
}

export function LocalNavigation({ navItems }: NavContentComponentProps) {
  return (
    <Sidebar>
      <Brand />
      {navItems.rest().map((item) => (
        <SidebarItem
          key={item.node.spec.id}
          to={item.href}
          text={item.title}
          icon={() => item.icon}
        />
      ))}
    </Sidebar>
  );
}
