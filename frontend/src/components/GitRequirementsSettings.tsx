import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, FormGroup } from '@patternfly/react-core';
import { useApmeApi } from '../api/apmeApiAdapter';
import { getGitRequirementsPolicy, setGitRequirementsPolicy } from '../services/api';

export function GitRequirementsSettings() {
  const api = useApmeApi();
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    getGitRequirementsPolicy(api).then(p => setEnabled(p.enabled))
      .catch(() => setError('Unable to load Git requirements policy.'))
      .finally(() => setLoading(false));
  }, [api]);
  async function save() {
    setSaving(true);
    setError('');
    try { await setGitRequirementsPolicy(enabled, api); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to save policy.'); }
    finally { setSaving(false); }
  }
  return <section style={{ marginTop: 32 }}>
    <h3>Git collections and roles</h3>
    {error && <Alert isInline variant="danger" title={error} />}
    <FormGroup fieldId="git-requirements-enabled">
      <Checkbox id="git-requirements-enabled" label="Allow Git requirements for all projects"
        isChecked={enabled} isDisabled={loading || saving}
        onChange={(_event, checked) => setEnabled(checked)} />
      <p>Install collections and roles declared in project requirements files from HTTPS Git repositories.
        Project Git credentials are used by default. Projects can configure a separate requirements credential.</p>
    </FormGroup>
    <Button onClick={save} isDisabled={loading || saving} isLoading={saving}>Save Git requirements setting</Button>
  </section>;
}
