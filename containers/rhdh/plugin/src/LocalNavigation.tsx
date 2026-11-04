/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  Sidebar,
  SidebarItem,
  useSidebarOpenState,
} from "@backstage/core-components";
import type { NavContentComponentProps } from "@backstage/plugin-app-react";
import { LocalThemeControl } from "./LocalThemeControl";
import { useState } from "react";

const productIcon = new URL(
  "../../../../frontend/public/apme.svg",
  import.meta.url,
).href;

function ProductIcon() {
  return (
    <img
      src={productIcon}
      alt="APME logo"
      aria-hidden="true"
      width="32"
      height="32"
    />
  );
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
      <div className="apme-local-nav-heading">
        <SidebarItem
          className="apme-local-nav-brand"
          icon={ProductIcon}
          text="APME"
          to="/"
          end
        />
        <button
          className="apme-local-nav-toggle"
          type="button"
          aria-label={
            isOpen && expanded
              ? "Collapse APME sections"
              : "Expand APME sections"
          }
          aria-expanded={isOpen && expanded}
          aria-controls="apme-local-sections"
          onClick={toggle}
        >
          <svg
            viewBox="0 0 24 24"
            width="18"
            height="18"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              d={
                expanded
                  ? "m7 9 5 5 5-5-1.4-1.4-3.6 3.6-3.6-3.6L7 9Z"
                  : "m9 7 5 5-5 5-1.4-1.4 3.6-3.6-3.6-3.6L9 7Z"
              }
            />
          </svg>
        </button>
      </div>
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
      {isOpen && <LocalThemeControl />}
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
