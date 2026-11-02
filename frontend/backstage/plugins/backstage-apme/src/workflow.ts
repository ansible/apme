/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
export {
  ApmeProjectWorkflow,
  type ApmeProjectWorkflowProps,
} from "./components/ApmeProjectWorkflow";
import { apmeProjectViews } from "./capabilities";
export const capability = apmeProjectViews.workflow;
