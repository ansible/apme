/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { HeaderTabs, Link } from "@backstage/core-components";

const SETTINGS_TABS = [
  { path: "/settings/general", title: "General" },
  { path: "/settings/auth-providers", title: "Authentication Providers" },
  { path: "/settings/abbenay-ai", title: "Abbenay AI" },
] as const;

/** Settings sub-nav with absolute links (RHDH dynamic routes are outside UserSettingsPage). */
export function ApmeSettingsTabBar() {
  const location = useLocation();

  const selectedIndex = useMemo(() => {
    const idx = SETTINGS_TABS.findIndex((tab) =>
      location.pathname.startsWith(tab.path),
    );
    return idx >= 0 ? idx : SETTINGS_TABS.length - 1;
  }, [location.pathname]);

  const tabs = useMemo(
    () =>
      SETTINGS_TABS.map((tab) => ({
        id: tab.path,
        label: tab.title,
        tabProps: {
          component: Link,
          to: tab.path,
        },
      })),
    [],
  );

  return <HeaderTabs tabs={tabs} selectedIndex={selectedIndex} />;
}
