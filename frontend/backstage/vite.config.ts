import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

const at = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  root: at("./dev"),
  plugins: [react()],
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: [
      {
        find: "@apme/ui-workflow/graph",
        replacement: at("../packages/ui-workflow/src/graph/index.ts"),
      },
      {
        find: "@testing-library/react",
        replacement: at("../../node_modules/@testing-library/react"),
      },
      {
        find: "@patternfly/react-core",
        replacement: at("../../node_modules/@patternfly/react-core"),
      },
      {
        find: "@patternfly/react-icons",
        replacement: at("../../node_modules/@patternfly/react-icons"),
      },
      {
        find: "typescript",
        replacement: at("../../node_modules/typescript/lib/typescript.js"),
      },
      { find: "react", replacement: at("../../node_modules/react") },
      { find: "react-dom", replacement: at("../../node_modules/react-dom") },
      {
        find: "@apme/client/transport",
        replacement: at("../packages/apme-client/src/transport.ts"),
      },
      {
        find: "@apme/client/types",
        replacement: at("../packages/apme-client/src/types.ts"),
      },
      {
        find: "@apme/client",
        replacement: at("../packages/apme-client/src/index.ts"),
      },
      {
        find: "@apme/ui-workflow/headless",
        replacement: at("../packages/ui-workflow/src/headless.ts"),
      },
      {
        find: "@apme/ui-workflow",
        replacement: at("../packages/ui-workflow/src/index.ts"),
      },
    ],
  },
  // Development harness only. Real hosts supply their authenticated backend.
  server: {
    port: 3001,
    proxy: {
      "/api/v1/ws": { target: "ws://127.0.0.1:8080", ws: true },
      "/api/v1": { target: "http://127.0.0.1:8080", changeOrigin: true },
    },
  },
  build: { outDir: at("./dist-dev"), emptyOutDir: true },
  test: {
    root: at("../.."),
    environment: "jsdom",
    globals: true,
    include: [
      "frontend/backstage/tests/**/*.test.{ts,tsx}",
      "frontend/packages/ui-workflow/src/**/*.test.{ts,tsx}",
    ],
    setupFiles: [at("./tests/setup.ts")],
  },
});
