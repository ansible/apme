import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Dialog,
  DialogBody,
  DialogFooter,
  DialogHeader,
  Link,
  Select,
  TextAreaField,
} from '@backstage/ui';
import { apmeApiUrl, useApmeApi } from '../api/apmeApiAdapter';

export interface FeedbackPayload {
  type: 'false_positive' | 'bad_ai_suggestion' | 'rule_misfire';
  rule_id: string;
  source: string;
  file: string;
  scan_id: string;
  context: {
    violation_message: string;
    ai_proposal_diff: string;
    ai_explanation: string;
    source_snippet: string;
  };
  user_comment: string;
}

export interface FeedbackModalProps {
  isOpen: boolean;
  onClose: () => void;
  prefill?: Partial<FeedbackPayload>;
  gatewayUrl?: string;
}

const ISSUE_TYPES = [
  {
    value: 'false_positive',
    label: 'False Positive — rule should not have fired',
  },
  {
    value: 'bad_ai_suggestion',
    label: 'Bad AI Suggestion — AI proposed wrong fix',
  },
  { value: 'rule_misfire', label: 'Rule Misfire — rule logic is incorrect' },
];

export function FeedbackModal({
  isOpen,
  onClose,
  prefill,
  gatewayUrl = '',
}: FeedbackModalProps) {
  const [issueType, setIssueType] = useState<string>(
    prefill?.type ?? 'false_positive',
  );
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && prefill?.type) setIssueType(prefill.type);
  }, [isOpen, prefill?.type]);

  const api = useApmeApi();

  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const payload: FeedbackPayload = {
        type: issueType as FeedbackPayload['type'],
        rule_id: prefill?.rule_id ?? '',
        source: prefill?.source ?? '',
        file: prefill?.file ?? '',
        scan_id: prefill?.scan_id ?? '',
        context: prefill?.context ?? {
          violation_message: '',
          ai_proposal_diff: '',
          ai_explanation: '',
          source_snippet: '',
        },
        user_comment: comment,
      };
      const { fetch: doFetch } = api;
      const url =
        gatewayUrl !== ''
          ? `${gatewayUrl.replace(/\/$/, '')}/api/v1/feedback`
          : apmeApiUrl('/feedback', api);
      const resp = await doFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const body = await resp
          .json()
          .catch(() => ({ detail: resp.statusText }));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setResult({ url: data.issue_url });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }, [api, issueType, comment, prefill, gatewayUrl]);

  const handleClose = useCallback(() => {
    setResult(null);
    setError(null);
    setComment('');
    onClose();
  }, [onClose]);

  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
      isDismissable
      width={640}
    >
      <DialogHeader>Report Issue</DialogHeader>
      <DialogBody>
        {result ? (
          <Alert
            status="success"
            title="Issue created"
            description={
              <Link href={result.url} target="_blank" rel="noopener noreferrer">
                {result.url}
              </Link>
            }
          />
        ) : (
          <form
            id="apme-feedback-form"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSubmit();
            }}
          >
            {error && (
              <Alert
                status="danger"
                role="alert"
                title="Submission failed"
                description={error}
              />
            )}

            <Select
              id="feedback-type"
              label="Issue type"
              isRequired
              selectionMode="single"
              value={issueType}
              onChange={(value) => {
                if (value != null) setIssueType(String(value));
              }}
              options={ISSUE_TYPES.map((type) => ({
                id: type.value,
                label: type.label,
              }))}
            />

            {prefill?.rule_id && (
              <div>
                <div>Rule</div>
                <code>{prefill.rule_id}</code>
                {prefill.source && (
                  <span style={{ marginLeft: 8, opacity: 0.7 }}>
                    ({prefill.source})
                  </span>
                )}
              </div>
            )}

            {prefill?.file && (
              <div>
                <div>File</div>
                <code>{prefill.file}</code>
              </div>
            )}

            <TextAreaField
              id="feedback-comment"
              label="Comment"
              value={comment}
              onChange={setComment}
              placeholder="Describe what went wrong..."
              rows={4}
            />
          </form>
        )}
      </DialogBody>
      <DialogFooter>
        {result ? (
          <Button onPress={handleClose}>Close</Button>
        ) : (
          <>
            <Button
              type="submit"
              form="apme-feedback-form"
              isPending={submitting}
              isDisabled={submitting}
            >
              Submit
            </Button>
            <Button variant="tertiary" onPress={handleClose}>
              Cancel
            </Button>
          </>
        )}
      </DialogFooter>
    </Dialog>
  );
}
