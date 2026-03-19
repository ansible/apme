import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["shared/src/**/*.test.{ts,tsx}", "standalone/src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
