import type { Entity } from "@backstage/catalog-model";
import { useSearchParams } from "react-router-dom";
import { EntityQualityTab } from ".";

/** Interpret APME's filters inside APME, never in the generic host. */
export function NativeEntityQualityTab({
  entity,
}: Readonly<{ entity: Entity }>) {
  const [params] = useSearchParams();
  return (
    <EntityQualityTab
      entity={entity}
      initialRuleFilter={params.get("rule") ?? undefined}
      initialCategoryFilter={params.get("category") ?? undefined}
    />
  );
}
