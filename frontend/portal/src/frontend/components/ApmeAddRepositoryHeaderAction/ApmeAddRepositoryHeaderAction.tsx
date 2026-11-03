/*
 * Copyright Red Hat
 *
 * ADR-010: Git Repositories page header action — contributed by APME guest plugin only.
 */

import AddIcon from "@material-ui/icons/Add";
import { LinkButton } from "@backstage/core-components";

/** Self-service Create path for the APME register-git-repository template. */
export const APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH =
  "/self-service/create/templates/default/apme-register-git-repository";

export const ApmeAddRepositoryHeaderAction = () => {
  return (
    <LinkButton
      variant="contained"
      color="primary"
      to={APME_REGISTER_GIT_REPOSITORY_TEMPLATE_PATH}
      startIcon={<AddIcon />}
    >
      Add repository
    </LinkButton>
  );
};
