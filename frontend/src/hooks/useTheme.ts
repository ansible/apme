import { useCallback, useEffect, useState } from "react";

type Theme = "dark" | "light";

function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem("apme-theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* SSR or blocked localStorage */
  }
  return "dark";
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("apme-theme", theme);
    } catch {
      /* blocked localStorage */
    }
  }, [theme]);

  const toggle = useCallback(() => {
    setThemeState((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle } as const;
}
