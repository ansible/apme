/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback } from "react";
import { Alert, Checkbox, Flex, Text } from "@backstage/ui";
import {
  getGitRequirementsPolicy,
  setGitRequirementsPolicy,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";

export function ApmeGitRequirementsSettings({
  adapter,
  canView,
  canManage,
}: {
  adapter: ApmeApiAdapter;
  canView?: boolean;
  canManage?: boolean;
}) {
  const load = useCallback(() => getGitRequirementsPolicy(adapter), [adapter]);
  const state = useLoad(load, canView === true);
  const mutation = useMutation(adapter);
  if (!canView) return null;
  return (
    <Flex direction="column" gap="4">
      <Text as="h3" variant="title-small">
        Git collections and roles
      </Text>
      {(state.error || mutation.error) && (
        <Alert
          status="danger"
          title="Could not load or save Git requirements policy"
          description={state.error || mutation.error}
        />
      )}
      <Checkbox
        isSelected={state.value?.enabled ?? false}
        isDisabled={
          !canManage || state.loading || !state.value || mutation.saving
        }
        onChange={(enabled) =>
          void mutation.run(
            () => setGitRequirementsPolicy(enabled, adapter).then(() => {}),
            state.reload,
          )
        }
      >
        Allow Git requirements for all projects
      </Checkbox>
      <Text>
        Fetch collections and roles declared in project requirements files from
        HTTPS Git repositories. Project SCM credentials are used by default.
        Projects can supply a separate requirements credential.
      </Text>
    </Flex>
  );
}
