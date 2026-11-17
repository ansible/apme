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

import { useEffect } from "react";
import { useTheme } from "@material-ui/core/styles";

/** PatternFly v6 dark theme class — tokens redefine on `:root:where(.pf-v6-theme-dark)`. */
export const PF_V6_THEME_DARK = "pf-v6-theme-dark";

let ownerCount = 0;
let baselineHadClass = false;
let baselineThemeMode: string | null = null;

/**
 * While the APME Quality host is mounted, mirror Backstage/MUI dark mode onto
 * ``document.documentElement`` so remaining PatternFly consumers and the
 * shared BUI workflow pick up their respective dark design tokens.
 *
 * Both theme markers live on the root so they also cover body-portaled UI.
 * On unmount, restore their states from before the first owner.
 */
export function useSyncPatternFlyTheme(): void {
  const theme = useTheme();
  const isDark = theme.palette.type === "dark";

  useEffect(() => {
    const root = document.documentElement;
    if (ownerCount === 0) {
      baselineHadClass = root.classList.contains(PF_V6_THEME_DARK);
      baselineThemeMode = root.getAttribute("data-theme-mode");
    }
    ownerCount += 1;

    if (isDark) {
      root.classList.add(PF_V6_THEME_DARK);
    } else {
      root.classList.remove(PF_V6_THEME_DARK);
    }
    root.setAttribute("data-theme-mode", isDark ? "dark" : "light");

    return () => {
      ownerCount -= 1;
      if (ownerCount === 0) {
        if (baselineHadClass) {
          root.classList.add(PF_V6_THEME_DARK);
        } else {
          root.classList.remove(PF_V6_THEME_DARK);
        }
        if (baselineThemeMode === null) {
          root.removeAttribute("data-theme-mode");
        } else {
          root.setAttribute("data-theme-mode", baselineThemeMode);
        }
      }
    };
  }, [isDark]);
}

/** Test helper — reset module owner state between cases. */
export function __resetPatternFlyThemeSyncForTests(): void {
  ownerCount = 0;
  baselineHadClass = false;
  baselineThemeMode = null;
  document.documentElement.classList.remove(PF_V6_THEME_DARK);
  document.documentElement.removeAttribute("data-theme-mode");
}
