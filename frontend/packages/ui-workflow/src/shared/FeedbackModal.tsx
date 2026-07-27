import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  TextArea,
} from '@patternfly/react-core';
import { apmeApiUrl, getApmeApiAdapter } from '../api/apmeApiAdapter';

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
  { value: 'false_positive', label: 'False Positive — rule should not have fired' },
  { value: 'bad_ai_suggestion', label: 'Bad AI Suggestion — AI proposed wrong fix' },
  { value: 'rule_misfire', label: 'Rule Misfire — rule logic is incorrect' },
];

export function FeedbackModal({ isOpen, onClose, prefill, gatewayUrl = '' }: FeedbackModalProps) {
  const [issueType, setIssueType] = useState<string>(prefill?.type ?? 'false_positive');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && prefill?.type) setIssueType(prefill.type);
  }, [isOpen, prefill?.type]);

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
        context: prefill?.context ?? { violation_message: '', ai_proposal_diff: '', ai_explanation: '', source_snippet: '' },
        user_comment: comment,
      };
      const { fetch: doFetch } = getApmeApiAdapter();
      const url =
        gatewayUrl !== ''
          ? `${gatewayUrl.replace(/\/$/, '')}/api/v1/feedback`
          : apmeApiUrl('/feedback');
      const resp = await doFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setResult({ url: data.issue_url });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }, [issueType, comment, prefill, gatewayUrl]);

  const handleClose = useCallback(() => {
    setResult(null);
    setError(null);
    setComment('');
    onClose();
  }, [onClose]);

  return (
    <Modal isOpen={isOpen} onClose={handleClose} variant="medium">
      <ModalHeader title="Report Issue" />
      <ModalBody>
        {result ? (
          <Alert variant="success" title="Issue created">
            <a href={result.url} target="_blank" rel="noopener noreferrer">{result.url}</a>
          </Alert>
        ) : (
          <Form>
            {error && <Alert variant="danger" title="Submission failed">{error}</Alert>}

            <FormGroup label="Issue type" isRequired fieldId="feedback-type">
              <FormSelect id="feedback-type" value={issueType} onChange={(_e, v) => setIssueType(v)}>
                {ISSUE_TYPES.map((t) => (
                  <FormSelectOption key={t.value} value={t.value} label={t.label} />
                ))}
              </FormSelect>
            </FormGroup>

            {prefill?.rule_id && (
              <FormGroup label="Rule" fieldId="feedback-rule">
                <code>{prefill.rule_id}</code>
                {prefill.source && <span style={{ marginLeft: 8, opacity: 0.7 }}>({prefill.source})</span>}
              </FormGroup>
            )}

            {prefill?.file && (
              <FormGroup label="File" fieldId="feedback-file">
                <code>{prefill.file}</code>
              </FormGroup>
            )}

            <FormGroup label="Comment" fieldId="feedback-comment">
              <TextArea
                id="feedback-comment"
                value={comment}
                onChange={(_e, v) => setComment(v)}
                placeholder="Describe what went wrong..."
                rows={4}
              />
            </FormGroup>
          </Form>
        )}
      </ModalBody>
      <ModalFooter>
        {result ? (
          <Button onClick={handleClose}>Close</Button>
        ) : (
          <>
            <Button onClick={handleSubmit} isLoading={submitting} isDisabled={submitting}>
              Submit
            </Button>
            <Button variant="link" onClick={handleClose}>Cancel</Button>
          </>
        )}
      </ModalFooter>
    </Modal>
  );
}
