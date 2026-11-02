/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  Sidebar,
  SidebarItem,
  useSidebarOpenState,
} from "@backstage/core-components";
import type { NavContentComponentProps } from "@backstage/plugin-app-react";
import { useState, type MouseEvent } from "react";

const productIcon = new URL(
  "../../../../frontend/public/apme.svg",
  import.meta.url,
).href;

function ProductIcon() {
  return <img src={productIcon} alt="" width="32" height="32" />;
}

function ApmeNavigation({ navItems }: NavContentComponentProps) {
  const { isOpen, setOpen } = useSidebarOpenState();
  const [expanded, setExpanded] = useState(true);
  const items = navItems.rest();
  const apmeItems = items.filter((item) => item.node.spec.plugin.id === "apme");
  const toggle = () => {
    if (!isOpen) {
      setOpen(true);
      setExpanded(true);
    } else setExpanded((value) => !value);
  };
  return (
    <>
      <SidebarItem
        icon={ProductIcon}
        text="APME"
        to="/"
        end
        role="button"
        aria-current="false"
        aria-label="APME"
        aria-expanded={isOpen && expanded}
        aria-controls="apme-local-sections"
        onClick={(event: MouseEvent) => {
          event.preventDefault();
          toggle();
        }}
        onKeyDown={(event) => {
          if (event.key === " ") {
            event.preventDefault();
            toggle();
          }
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="currentColor"
          aria-hidden
        >
          <path
            d={
              expanded
                ? "m7 9 5 5 5-5-1.4-1.4-3.6 3.6-3.6-3.6L7 9Z"
                : "m9 7 5 5-5 5-1.4-1.4 3.6-3.6-3.6-3.6L9 7Z"
            }
          />
        </svg>
      </SidebarItem>
      <div id="apme-local-sections" hidden={!isOpen || !expanded}>
        {apmeItems.map((item) => (
          <SidebarItem
            key={item.node.spec.id}
            to={item.href}
            end
            text={item.title}
            icon={() => item.icon}
            className="apme-local-nav-child"
          />
        ))}
      </div>
      {items
        .filter((item) => item.node.spec.plugin.id !== "apme")
        .map((item) => (
          <SidebarItem
            key={item.node.spec.id}
            to={item.href}
            text={
              item.node.spec.id === "page:user-settings"
                ? "User settings"
                : item.title
            }
            icon={() => item.icon}
          />
        ))}
    </>
  );
}

export function LocalNavigation(props: NavContentComponentProps) {
  return (
    <Sidebar>
      <ApmeNavigation {...props} />
    </Sidebar>
  );
}
