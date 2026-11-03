/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useEffect, useState, type ReactElement } from "react";
import {
  configApiRef,
  identityApiRef,
  useApiHolder,
} from "@backstage/core-plugin-api";
import { portalEntitlementsApiRef } from "@ansible/portal-extension-api";

/** Catalog-level SDK descriptors do not all support entitlement gates yet.
 * Keep APME loaders fail-closed in those hosts as well as declaring the metadata.
 */
function PortalContributionGate({
  load,
  entityRef,
  requiredEntitlements,
}: {
  load: () => Promise<ReactElement>;
  entityRef?: string;
  requiredEntitlements: readonly string[];
}) {
  const holder = useApiHolder();
  const config = holder.get(configApiRef);
  const identity = holder.get(identityApiRef);
  const entitlements = holder.get(portalEntitlementsApiRef);
  const [content, setContent] = useState<ReactElement | null>(null);
  useEffect(() => {
    let active = true;
    setContent(null);
    async function resolve() {
      if (config?.getOptionalBoolean("ansible.apme.enabled") !== true) return;
      for (const id of requiredEntitlements) {
        if (!identity || !entitlements) return;
        const subject =
          entityRef ?? (await identity.getBackstageIdentity()).userEntityRef;
        const decision = await entitlements.check(id, subject);
        if (!decision.allowed || !active) return;
      }
      const element = await load();
      if (active) setContent(element);
    }
    void resolve().catch(() => {
      if (active) setContent(null);
    });
    return () => {
      active = false;
    };
  }, [config, identity, entitlements, entityRef, load, requiredEntitlements]);
  return content;
}

export function withPortalGate<
  T extends (...args: any[]) => Promise<ReactElement>,
>(loader: T, requiredEntitlements: readonly string[] = ["apme.scanning"]): T {
  return (async (...args: Parameters<T>) => {
    const entity = args[0]?.entity;
    const entityRef = entity
      ? `${entity.kind.toLowerCase()}:${entity.metadata.namespace ?? "default"}/${entity.metadata.name}`
      : undefined;
    return (
      <PortalContributionGate
        entityRef={entityRef}
        requiredEntitlements={requiredEntitlements}
        load={() => loader(...args)}
      />
    );
  }) as T;
}
