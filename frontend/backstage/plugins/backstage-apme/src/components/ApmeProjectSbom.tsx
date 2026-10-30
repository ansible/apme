/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Flex, Text } from "@backstage/ui";
import { getProjectSbom } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { useMutation } from "./useMutation";

export interface ApmeProjectSbomProps {
  projectId: string;
  adapter: ApmeApiAdapter;
}

export function ApmeProjectSbom(props: ApmeProjectSbomProps) {
  return <ProjectSbomContent key={props.projectId} {...props} />;
}

function ProjectSbomContent({ projectId, adapter }: ApmeProjectSbomProps) {
  const mutation = useMutation(adapter);
  const [download, setDownload] = useState<{
    url: string;
    file: string;
    adapter: ApmeApiAdapter;
  }>();
  const currentUrl = useRef<string>();
  // Drop any previous project's/principal's prepared download on adapter change.
  useEffect(() => {
    setDownload(undefined);
    return () => {
      if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
      currentUrl.current = undefined;
    };
  }, [adapter]);
  async function prepare() {
    // Store the Blob only if this view is still mounted when the request finishes.
    let blob: Blob | undefined;
    await mutation.run(
      async () => {
        blob = await getProjectSbom(projectId, adapter);
      },
      () => {
        if (!blob) return;
        if (currentUrl.current) URL.revokeObjectURL(currentUrl.current);
        const url = URL.createObjectURL(blob);
        currentUrl.current = url;
        const safeId =
          projectId.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120) || "project";
        setDownload({ url, file: `sbom-${safeId}.cdx.json`, adapter });
      },
    );
  }
  return (
    <Flex direction="column" gap="2">
      <Button
        variant="secondary"
        isDisabled={mutation.saving}
        onPress={() => void prepare()}
      >
        {mutation.saving ? "Preparing SBOM…" : "Prepare SBOM (CycloneDX)"}
      </Button>
      {mutation.error && (
        <Alert
          status="danger"
          title="Could not download SBOM"
          description={mutation.error}
        />
      )}
      {download?.adapter === adapter && (
        <a href={download.url} download={download.file}>
          Download SBOM (CycloneDX)
        </a>
      )}
      <Text>Uses dependencies recorded by the latest check.</Text>
    </Flex>
  );
}
