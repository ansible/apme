/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
export {
  ApmeProjectDependencies,
  type ApmeProjectDependenciesProps,
} from "./components/ApmeProjectDependencies";
export {
  ApmeProjectSbom,
  type ApmeProjectSbomProps,
} from "./components/ApmeProjectSbom";
import { apmeProjectViews } from "./capabilities";
export const capability = apmeProjectViews.dependencies;
