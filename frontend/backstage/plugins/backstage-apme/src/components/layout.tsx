/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { forwardRef, type ReactNode, type ComponentProps } from "react";
import { Button, Dialog, DialogFooter, Text, TableRoot } from "@backstage/ui";
import "./layout.css";

/** Explicit opt-in keeps ordinary controls independent of workflow styling. */
export const ApmeButton = forwardRef<
  HTMLButtonElement,
  ComponentProps<typeof Button>
>(({ className, size = "small", ...props }, ref) => (
  <Button
    {...props}
    size={size}
    ref={ref}
    className={`apme-button ${className ?? ""}`}
  />
));
ApmeButton.displayName = "ApmeButton";

/** Portal dialogs need their own palette and scope outside the page subtree. */
export function ApmeDialog({
  className,
  width = "min(42rem, calc(100vw - 32px))",
  ...props
}: ComponentProps<typeof Dialog>) {
  return (
    <Dialog
      {...props}
      width={width}
      className={`apme-ui apme-dialog ${className ?? ""}`}
    />
  );
}

export function TreeOutput({ text, label }: { text: string; label: string }) {
  return (
    <pre
      className="apme-tree-output"
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      {text}
    </pre>
  );
}

/** Keep action order stable even when a host reverses its default footer. */
export function ApmeDialogFooter({
  style,
  ...props
}: ComponentProps<typeof DialogFooter>) {
  return (
    <DialogFooter
      {...props}
      style={{
        ...style,
        display: "flex",
        flexDirection: "row",
        justifyContent: "flex-end",
        gap: 8,
        flexWrap: "wrap",
      }}
    />
  );
}

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
