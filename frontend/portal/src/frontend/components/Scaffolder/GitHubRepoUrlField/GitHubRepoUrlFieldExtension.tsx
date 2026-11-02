/*
 * Copyright Red Hat
 *
 * Scaffolder field: paste a github.com URL; show parsed owner/repo; store
 * RepoUrlPicker-compatible value for template `parseRepoUrl`.
 */

import { useMemo, useState } from "react";
import { TextField, Typography, Box } from "@material-ui/core";
import type { FieldExtensionComponentProps } from "@backstage/plugin-scaffolder-react";
import { parseGitHubComRepoUrl } from "./parseGitHubComRepoUrl";

function displayUrlFromPickerValue(value?: string): string {
  if (!value) {
    return "";
  }
  // Already a pasted URL in progress (before valid parse stored picker format).
  if (
    value.includes("://") ||
    value.startsWith("git@") ||
    value.startsWith("github.com/")
  ) {
    return value;
  }
  try {
    const params = new URLSearchParams(
      value.includes("?") ? value.split("?")[1] : "",
    );
    const owner = params.get("owner");
    const repo = params.get("repo");
    if (owner && repo) {
      return `https://github.com/${owner}/${repo}`;
    }
  } catch {
    // fall through
  }
  return value;
}

export const GitHubRepoUrlFieldExtension = ({
  onChange,
  rawErrors,
  required,
  formData,
  schema,
  uiSchema,
  disabled,
  idSchema,
}: FieldExtensionComponentProps<string>) => {
  const [draft, setDraft] = useState(() => displayUrlFromPickerValue(formData));

  const parsed = useMemo(() => parseGitHubComRepoUrl(draft), [draft]);

  const title = schema?.title ?? "GitHub repository URL";
  const customHelper = uiSchema?.["ui:options"]?.helperText;
  const helperFromOptions =
    typeof customHelper === "string" ? customHelper : undefined;

  const helperText = (() => {
    if (rawErrors?.length) {
      return rawErrors.join(", ");
    }
    if (draft.trim() && !parsed.ok) {
      return parsed.error;
    }
    if (parsed.ok) {
      const branchHint = parsed.value.suggestedBranch
        ? ` · branch from URL: ${parsed.value.suggestedBranch}`
        : "";
      return `Organization: ${parsed.value.owner} · Repository: ${parsed.value.repo}${branchHint}`;
    }
    return (
      helperFromOptions ??
      "Paste a github.com URL (https://github.com/owner/repo). Other hosts are not supported yet."
    );
  })();

  return (
    <Box>
      <TextField
        id={idSchema?.$id}
        label={title}
        value={draft}
        required={required}
        disabled={disabled}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          const result = parseGitHubComRepoUrl(next);
          if (result.ok) {
            onChange(result.value.repoUrlPicker);
          } else if (!next.trim()) {
            onChange("");
          } else {
            // Keep draft visible but clear committed value so Next stays blocked.
            onChange("");
          }
        }}
        onBlur={() => {
          const result = parseGitHubComRepoUrl(draft);
          if (result.ok) {
            setDraft(result.value.httpsUrl);
            onChange(result.value.repoUrlPicker);
          }
        }}
        fullWidth
        variant="outlined"
        margin="normal"
        error={
          Boolean(rawErrors?.length) || (Boolean(draft.trim()) && !parsed.ok)
        }
        helperText={helperText}
        placeholder="https://github.com/owner/repo"
        inputProps={{ "aria-label": title }}
      />
      {parsed.ok ? (
        <Typography
          variant="body2"
          color="textSecondary"
          style={{ marginTop: -4 }}
        >
          Will register{" "}
          <strong>
            {parsed.value.owner}/{parsed.value.repo}
          </strong>
        </Typography>
      ) : null}
    </Box>
  );
};
