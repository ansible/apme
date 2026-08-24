/**
 * Pack-time `tsc -p tsconfig.build.json` does not see frontend/src/globals.d.ts.
 * TypeScript 7 reports TS2882 on the side-effect CSS import in index.ts without this.
 */
declare module '*.css';
