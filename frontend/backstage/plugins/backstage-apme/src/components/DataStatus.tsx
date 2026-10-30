/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { Alert, Button, Flex, Text } from "@backstage/ui";

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
    <Flex gap="2" justify="end">
      <Button
        variant="secondary"
        isDisabled={busy || offset === 0}
        onPress={() => onChange(Math.max(0, offset - size))}
      >
        Previous {subject} page
      </Button>
      <Button
        variant="secondary"
        isDisabled={busy || !hasNext}
        onPress={() => onChange(offset + size)}
      >
        Next {subject} page
      </Button>
    </Flex>
  );
}
