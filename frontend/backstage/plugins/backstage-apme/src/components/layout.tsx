/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { ReactNode, ComponentProps } from "react";
import { Text, TableRoot } from "@backstage/ui";
import "./layout.css";

/** Shared visual rhythm for pages and independently embedded project views. */
export function SectionHeader({
  title,
  description,
  actions,
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="apme-section-header">
      <div className="apme-section-heading">
        {title && (
          <Text
            as="h2"
            variant="title-medium"
            style={{ fontSize: 20, lineHeight: "28px" }}
          >
            {title}
          </Text>
        )}
        {description && (
          <Text as="p" color="secondary">
            {description}
          </Text>
        )}
      </div>
      {actions && <div className="apme-actions">{actions}</div>}
    </div>
  );
}

/** Keep wide data tables inside their host slot at narrow viewport widths. */
export function ApmeTable(props: ComponentProps<typeof TableRoot>) {
  return (
    <div className="apme-ui apme-table-scroll">
      <TableRoot {...props} />
    </div>
  );
}
