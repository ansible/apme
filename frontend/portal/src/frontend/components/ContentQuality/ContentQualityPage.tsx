import { Navigate } from "react-router-dom";
import { RequirePermission } from "@backstage/plugin-permission-react";
import { gitRepositoriesViewPermission } from "@ansible/backstage-rhaap-common/permissions";
import { useApmeEnabled } from "../../hooks/useApmeEnabled";

/** Compatibility entry into the host's existing contributed catalog tab. */
export function ContentQualityPage({
  catalogTabId,
}: Readonly<{ catalogTabId: string }>) {
  const enabled = useApmeEnabled();
  return (
    <RequirePermission
      permission={gitRepositoriesViewPermission}
      errorPage={
        <p role="alert">You do not have permission to view Content Quality.</p>
      }
    >
      {enabled ? (
        <Navigate
          replace
          to={{
            // Bypass the catalog index redirect, which does not preserve search.
            pathname: "/self-service/repositories/catalog",
            search: `?${new URLSearchParams({ catalogTab: catalogTabId })}`,
          }}
        />
      ) : (
        <output>
          Content Quality is unavailable because APME is disabled.
        </output>
      )}
    </RequirePermission>
  );
}
