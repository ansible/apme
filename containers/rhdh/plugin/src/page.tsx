/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { BUIProvider } from "@backstage/ui";
import { ApmePageContent } from "@apme/backstage-plugin/page";
import "@backstage/ui/css/styles.css";
import "@patternfly/react-core/dist/styles/base.css";

export function LocalApmePage() {
  return (
    <BUIProvider>
      <ApmePageContent />
    </BUIProvider>
  );
}
