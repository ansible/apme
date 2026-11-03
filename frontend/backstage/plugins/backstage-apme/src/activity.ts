/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
export {
  ApmeProjectHistory,
  type ApmeProjectHistoryProps,
} from "./components/ApmeProjectHistory";
export {
  ApmeActivityDetails,
  type ApmeActivityDetailsProps,
} from "./components/ApmeActivityDetails";
import { apmeProjectViews } from "./capabilities";
export const capability = apmeProjectViews.activity;
