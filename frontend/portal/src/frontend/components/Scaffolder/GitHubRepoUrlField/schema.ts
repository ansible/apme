/*
 * Copyright Red Hat
 */

import { makeFieldSchema } from "@backstage/plugin-scaffolder-react";

/** Field schema for GitHubRepoUrlField (stores RepoUrlPicker-compatible string). */
export const GitHubRepoUrlFieldSchema = makeFieldSchema({
  output: (z) => z.string(),
  uiOptions: (z) =>
    z.object({
      helperText: z.string().optional(),
    }),
});

export type GitHubRepoUrlFieldUiOptions = NonNullable<
  (typeof GitHubRepoUrlFieldSchema.TProps.uiSchema)["ui:options"]
>;

export type GitHubRepoUrlFieldProps = typeof GitHubRepoUrlFieldSchema.TProps;

export const GitHubRepoUrlFieldFieldSchema = GitHubRepoUrlFieldSchema.schema;
