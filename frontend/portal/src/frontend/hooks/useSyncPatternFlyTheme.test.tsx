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

import { renderHook } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import type { ReactNode } from "react";
import {
  PF_V6_THEME_DARK,
  __resetPatternFlyThemeSyncForTests,
  useSyncPatternFlyTheme,
} from "./useSyncPatternFlyTheme";

function wrapperFor(type: "light" | "dark") {
  const theme = createTheme({ palette: { type } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <ThemeProvider theme={theme}>{children}</ThemeProvider>;
  };
}

describe("useSyncPatternFlyTheme", () => {
  beforeEach(() => {
    __resetPatternFlyThemeSyncForTests();
  });

  afterEach(() => {
    __resetPatternFlyThemeSyncForTests();
  });

  it("sets the root PatternFly and BUI markers for a dark theme", () => {
    const { unmount } = renderHook(() => useSyncPatternFlyTheme(), {
      wrapper: wrapperFor("dark"),
    });

    expect(document.documentElement.classList.contains(PF_V6_THEME_DARK)).toBe(
      true,
    );
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe(
      "dark",
    );

    unmount();
    expect(document.documentElement.classList.contains(PF_V6_THEME_DARK)).toBe(
      false,
    );
    expect(document.documentElement.getAttribute("data-theme-mode")).toBeNull();
  });

  it("does not add the class when MUI theme is light", () => {
    const { unmount } = renderHook(() => useSyncPatternFlyTheme(), {
      wrapper: wrapperFor("light"),
    });

    expect(document.documentElement.classList.contains(PF_V6_THEME_DARK)).toBe(
      false,
    );
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe(
      "light",
    );
    unmount();
  });

  it("restores a pre-existing dark class after unmount", () => {
    document.documentElement.classList.add(PF_V6_THEME_DARK);

    const { unmount } = renderHook(() => useSyncPatternFlyTheme(), {
      wrapper: wrapperFor("light"),
    });

    expect(document.documentElement.classList.contains(PF_V6_THEME_DARK)).toBe(
      false,
    );

    unmount();
    expect(document.documentElement.classList.contains(PF_V6_THEME_DARK)).toBe(
      true,
    );

    document.documentElement.classList.remove(PF_V6_THEME_DARK);
  });

  it("restores a pre-existing BUI theme mode after unmount", () => {
    document.documentElement.setAttribute("data-theme-mode", "auto");

    const { unmount } = renderHook(() => useSyncPatternFlyTheme(), {
      wrapper: wrapperFor("dark"),
    });

    expect(document.documentElement.getAttribute("data-theme-mode")).toBe(
      "dark",
    );

    unmount();
    expect(document.documentElement.getAttribute("data-theme-mode")).toBe(
      "auto",
    );
  });
});
