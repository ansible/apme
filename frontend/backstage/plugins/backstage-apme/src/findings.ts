/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
export {
  ApmeProjectFindings,
  type ApmeProjectFindingsProps,
} from "./components/ApmeProjectFindings";
import { apmeProjectViews } from "./capabilities";
export const capability = apmeProjectViews.findings;
