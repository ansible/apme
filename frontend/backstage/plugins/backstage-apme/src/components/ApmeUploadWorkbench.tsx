/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Flex,
  Text,
  TextAreaField,
  TextField,
} from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import {
  ApmeApiProvider,
  AI_MODEL_STORAGE_KEY,
  getPersistedSession,
  useSessionStream,
} from "@apme/ui-workflow/headless";
import {
  CheckOptionsForm,
  OperationProgressPanel,
  ProposalReviewPanel,
  Tier1ResultsPanel,
  type OperationStatus,
  type OperationProposal,
  type Patch,
} from "@apme/ui-workflow";
import { DisclosureSection } from "./DisclosureSection";
import { useMutation } from "./useMutation";

export interface ApmeUploadWorkbenchProps {
  adapter: ApmeApiAdapter;
  canUpload?: boolean;
}
export function ApmeUploadWorkbench({
  adapter,
  canUpload = false,
}: ApmeUploadWorkbenchProps) {
  if (!canUpload) return <Text>Uploads are unavailable for this account.</Text>;
  if (!adapter.openSessionSocket)
    return (
      <Alert
        status="warning"
        title="Upload transport is unavailable"
        description="The host administrator must enable authenticated upload sessions."
      />
    );
  return (
    <ApmeApiProvider adapter={adapter}>
      <UploadContent />
    </ApmeApiProvider>
  );
}
function UploadContent() {
  const session = useSessionStream();
  const [files, setFiles] = useState<File[]>([]);
  const [paste, setPaste] = useState("");
  const [filename, setFilename] = useState("playbook.yml");
  const [ansibleVersion, setAnsibleVersion] = useState("");
  const [collections, setCollections] = useState("");
  const [enableAi, setEnableAi] = useState(true);
  const [autoApplyTier1, setAutoApplyTier1] = useState(false);
  const [inputError, setInputError] = useState<string>();
  const directory = useRef<HTMLInputElement>(null);
  const attemptedResume = useRef(false);
  useEffect(() => {
    directory.current?.setAttribute("webkitdirectory", "");
  }, [session.status]);
  useEffect(() => {
    if (attemptedResume.current) return;
    attemptedResume.current = true;
    const saved = getPersistedSession();
    if (saved) void session.resumeSession(saved.sessionId, saved.scanId);
  }, [session.resumeSession]);
  async function start() {
    setInputError(undefined);
    if (paste.trim() && (!filename || !safePath(filename))) {
      setInputError(
        "Use a relative filename without parent-directory segments.",
      );
      return;
    }
    const selected = [
      ...files,
      ...(paste.trim()
        ? [new File([paste], filename, { type: "text/yaml" })]
        : []),
    ];
    const paths = selected.map((file) => file.webkitRelativePath || file.name);
    if (
      !selected.length ||
      paths.some((path) => !safePath(path)) ||
      new Set(paths).size !== paths.length
    ) {
      setInputError("Select files with unique relative paths.");
      return;
    }
    let aiModel: string | undefined;
    try {
      aiModel = localStorage.getItem(AI_MODEL_STORAGE_KEY) || undefined;
    } catch {
      /* Engine default remains available. */
    }
    await session.startSession(selected, {
      ansibleVersion,
      collections: collections
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      enableAi,
      aiModel,
      interactive: !autoApplyTier1,
    });
  }
  const running = ["connecting", "uploading", "checking", "applying"].includes(
    session.status,
  );
  const patches = [
    ...(session.tier1?.patches ?? []),
    ...(session.result?.patches ?? []),
  ];
  return (
    <Flex direction="column" gap="4">
      <Text as="h2" variant="title-medium">
        Paste or upload Ansible content
      </Text>
      <Text>
        Check uploaded content and review fixes without registering a
        repository.
      </Text>
      {inputError && <Alert status="danger" title={inputError} />}
      {session.error && (
        <Alert
          status="danger"
          title="Upload session"
          description={session.error}
        />
      )}
      {session.status === "idle" && (
        <>
          <TextField
            label="Pasted file name"
            value={filename}
            onChange={setFilename}
          />
          <TextAreaField
            label="Paste Ansible content"
            value={paste}
            onChange={setPaste}
          />
          <label>
            Select files
            <input
              type="file"
              multiple
              onChange={(event) => {
                setFiles((previous) => [
                  ...previous,
                  ...Array.from(event.target.files ?? []),
                ]);
                event.target.value = "";
              }}
            />
          </label>
          <label>
            Select directory
            <input
              ref={directory}
              type="file"
              multiple
              onChange={(event) => {
                setFiles((previous) => [
                  ...previous,
                  ...Array.from(event.target.files ?? []),
                ]);
                event.target.value = "";
              }}
            />
          </label>
          <ul>
            {files.map((file, index) => (
              <li key={`${file.name}-${index}`}>
                {file.webkitRelativePath || file.name}{" "}
                <Button
                  variant="tertiary"
                  onPress={() =>
                    setFiles((previous) =>
                      previous.filter((_, n) => n !== index),
                    )
                  }
                >
                  Remove {file.name}
                </Button>
              </li>
            ))}
          </ul>
          <CheckOptionsForm
            ansibleVersion={ansibleVersion}
            onAnsibleVersionChange={setAnsibleVersion}
            collections={collections}
            onCollectionsChange={setCollections}
            enableAi={enableAi}
            onEnableAiChange={setEnableAi}
            autoApplyTier1={autoApplyTier1}
            onAutoApplyTier1Change={setAutoApplyTier1}
            idPrefix="upload"
          />
          <Button
            isDisabled={!files.length && !paste.trim()}
            onPress={() => void start()}
          >
            Check uploaded content
          </Button>
        </>
      )}
      {running && (
        <OperationProgressPanel
          status={
            (session.status === "uploading"
              ? "preparing"
              : session.status) as OperationStatus
          }
          progress={session.progress}
          onCancel={session.cancel}
        />
      )}
      {session.canReconnect && session.sessionId && (
        <Button
          onPress={() =>
            void session.resumeSession(
              session.sessionId!,
              session.scanId ?? undefined,
            )
          }
        >
          Reconnect upload session
        </Button>
      )}
      {session.tier1 && <Tier1ResultsPanel tier1={session.tier1} />}
      {session.status === "awaiting_approval" && (
        <ProposalReviewPanel
          proposals={session.proposals as OperationProposal[]}
          onApprove={session.approve}
          feedbackEnabled={false}
          scanId={session.scanId ?? undefined}
        />
      )}
      {session.result && (
        <>
          <Text as="h3" variant="title-small">
            Check complete
          </Text>
          <Text>
            {session.result.remaining_violations.length} remaining findings
          </Text>
          <DisclosureSection title="Remaining findings">
            <ul>
              {session.result.remaining_violations.map((item, index) => (
                <li key={index}>
                  {item.rule_id} · {item.file} · {item.message}
                </li>
              ))}
            </ul>
          </DisclosureSection>
          <PatchedDownloads patches={patches} />
        </>
      )}
      {["complete", "error", "disconnected", "tier1_done"].includes(
        session.status,
      ) && (
        <Button
          variant="secondary"
          onPress={() => {
            session.reset();
            setFiles([]);
            setPaste("");
          }}
        >
          Start new upload
        </Button>
      )}
    </Flex>
  );
}
function safePath(path: string) {
  return (
    !path.includes("\\") &&
    !path.includes(":") &&
    path.split("/").every((part) => !!part && part !== "." && part !== "..")
  );
}
function PatchedDownloads({ patches }: { patches: Patch[] }) {
  const mutation = useMutation();
  const [links, setLinks] = useState<Array<{ path: string; url: string }>>([]);
  const urls = useRef<string[]>([]);
  useEffect(
    () => () => {
      urls.current.forEach((url) => URL.revokeObjectURL(url));
    },
    [],
  );
  const available = [
    ...new Map(
      patches
        .filter(
          (patch) => typeof patch.patched === "string" && safePath(patch.file),
        )
        .map((patch) => [patch.file, patch]),
    ).values(),
  ];
  function prepare() {
    let archive: Blob | undefined;
    void mutation.run(
      async () => {
        const { default: JSZip } = await import("jszip");
        const zip = new JSZip();
        for (const patch of available)
          zip.file(patch.file, patch.patched!, { base64: true });
        archive = await zip.generateAsync({ type: "blob" });
      },
      () => {
        if (!archive) return;
        urls.current.forEach((url) => URL.revokeObjectURL(url));
        const url = URL.createObjectURL(archive);
        urls.current = [url];
        setLinks([{ path: "apme-remediated.zip", url }]);
      },
    );
  }
  return (
    <Flex direction="column" gap="2">
      {available.length > 0 && (
        <Button
          variant="secondary"
          onPress={prepare}
          isDisabled={mutation.saving}
        >
          Prepare remediated files
        </Button>
      )}
      {mutation.error && (
        <Alert
          status="danger"
          title="Could not prepare files"
          description={mutation.error}
        />
      )}
      <ul>
        {links.map((link) => (
          <li key={link.path}>
            <a href={link.url} download={link.path.split("/").pop()}>
              {link.path}
            </a>
          </li>
        ))}
      </ul>
    </Flex>
  );
}
