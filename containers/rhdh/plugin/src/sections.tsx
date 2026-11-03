/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { createRouteRef } from "@backstage/frontend-plugin-api";
import { projectsIcon, projectsRouteRef } from "@apme/backstage-plugin/alpha";

function icon(path: string) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="24"
      height="24"
      fill="currentColor"
      aria-hidden
    >
      <path d={path} />
    </svg>
  );
}

export const sections = [
  {
    name: undefined,
    section: "repositories",
    title: "Projects",
    path: "/",
    routeRef: projectsRouteRef,
    icon: projectsIcon,
  },
  {
    name: "rules",
    section: "rules",
    title: "Rules",
    path: "/apme/rules",
    routeRef: createRouteRef(),
    icon: icon(
      "M4 3h16v18H4V3Zm2 2v14h12V5H6Zm2 3h8v2H8V8Zm0 4h8v2H8v-2Zm0 4h5v2H8v-2Z",
    ),
  },
  {
    name: "health",
    section: "health",
    title: "Health",
    path: "/apme/health",
    routeRef: createRouteRef(),
    icon: icon(
      "M3 4h18v16H3V4Zm2 2v12h14V6H5Zm2 5h3l2-4 3 7 1-3h2v2h-1l-2 5-3-7-1 2H7v-2Z",
    ),
  },
  {
    name: "upload",
    section: "upload",
    title: "Paste / upload",
    path: "/apme/upload",
    routeRef: createRouteRef(),
    icon: icon(
      "M11 16V7L7 11 5.6 9.6 12 3.2l6.4 6.4L17 11l-4-4v9h-2ZM4 16h2v3h12v-3h2v5H4v-5Z",
    ),
  },
  {
    name: "settings",
    section: "settings",
    title: "Settings",
    path: "/apme/settings",
    routeRef: createRouteRef(),
    icon: icon(
      "M4 5h16v2H4V5Zm0 6h16v2H4v-2Zm0 6h16v2H4v-2ZM8 3h2v6H8V3Zm6 6h2v6h-2V9Zm-6 6h2v6H8v-6Z",
    ),
  },
] as const;

export function sectionPath(view: string) {
  return sections.find((item) => item.section === view)?.path;
}
