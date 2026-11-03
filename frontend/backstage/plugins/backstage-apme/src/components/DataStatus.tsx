/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeButton as Button } from "./layout";
import { Alert, Flex, Text } from "@backstage/ui";

export function DataStatus({
  loading,
  error,
  reload,
  subject,
}: {
  loading: boolean;
  error?: string;
  reload: () => void;
  subject: string;
}) {
  if (error)
    return (
      <Alert
        className="apme-ui"
        status="danger"
        title={`Could not load ${subject}`}
        description={error}
        customActions={
          <Button variant="secondary" onPress={reload}>
            Retry
          </Button>
        }
      />
    );
  return loading ? <Text role="status">Loading {subject}…</Text> : null;
}

export function PageControls({
  offset,
  size,
  hasNext,
  busy,
  onChange,
  subject,
}: {
  offset: number;
  size: number;
  hasNext: boolean;
  busy: boolean;
  onChange: (offset: number) => void;
  subject: string;
}) {
  return (
    <nav
      className="apme-ui apme-pagination"
      aria-label={`${subject} pagination`}
    >
      <span>Page {Math.floor(offset / size) + 1}</span>
      <Button
        variant="secondary"
        aria-label={`Previous ${subject} page`}
        isDisabled={busy || offset === 0}
        onPress={() => onChange(Math.max(0, offset - size))}
      >
        Previous
      </Button>
      <Button
        variant="secondary"
        aria-label={`Next ${subject} page`}
        isDisabled={busy || !hasNext}
        onPress={() => onChange(offset + size)}
      >
        Next
      </Button>
    </nav>
  );
}
