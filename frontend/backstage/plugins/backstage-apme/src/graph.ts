/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
export {
  ApmeProjectGraph,
  type ApmeProjectGraphProps,
} from "./components/ApmeProjectGraph";
import { apmeProjectViews } from "./capabilities";
export const capability = apmeProjectViews.graph;
