import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageLayout, PageHeader } from '@ansible/ansible-ui-framework';
import {
  Button,
  EmptyState,
  EmptyStateBody,
  Flex,
  FlexItem,
  Label,
  MenuToggle,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  SearchInput,
  TextInput,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
  Dropdown,
  DropdownItem,
  DropdownList,
} from '@patternfly/react-core';
import {
  EllipsisVIcon,
  PlusCircleIcon,
  SortAmountDownIcon,
  SortAmountUpIcon,
} from '@patternfly/react-icons';
import { createProject, deleteProject, listProjects } from '../services/api';
import type { ProjectSummary } from '../types/api';
import { timeAgo } from '../services/format';
import { healthLabelColor } from '../components/severity';

type SortField = 'name' | 'health_score' | 'total_violations' | 'scan_count' | 'last_scanned_at';

function HealthBadge({ score }: { score: number }) {
  return <Label color={healthLabelColor(score)} isCompact>{score}</Label>;
}

function TrendBadge({ trend }: { trend: string }) {
  if (trend === 'improving') return <Label color="green" isCompact>&#9650; Improving</Label>;
  if (trend === 'declining') return <Label color="red" isCompact>&#9660; Declining</Label>;
  return <Label isCompact>&#8212; Stable</Label>;
}

function StatusLabel({ lastScanned }: { lastScanned: string | null }) {
  if (!lastScanned) return <Label color="red" variant="outline" isCompact>Never checked</Label>;
  const daysSince = Math.floor((Date.now() - new Date(lastScanned).getTime()) / 86_400_000);
  if (daysSince > 30) return <Label color="orange" isCompact>Stale</Label>;
  return <Label color="green" isCompact>Idle</Label>;
}

export function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortAsc, setSortAsc] = useState(true);

  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createUrl, setCreateUrl] = useState('');
  const [createBranch, setCreateBranch] = useState('main');
  const [createScmToken, setCreateScmToken] = useState('');
  const [creating, setCreating] = useState(false);
  const [nameManuallyEdited, setNameManuallyEdited] = useState(false);
  const [openKebab, setOpenKebab] = useState<string | null>(null);

  const deriveNameFromUrl = useCallback((url: string): string => {
    try {
      const cleaned = url.replace(/\/+$/, '').replace(/\.git$/, '');
      const lastSegment = cleaned.split('/').pop() || '';
      return lastSegment
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, (c) => c.toUpperCase())
        .trim();
    } catch {
      return '';
    }
  }, []);

  const handleUrlChange = useCallback((_e: unknown, v: string) => {
    setCreateUrl(v);
    if (!nameManuallyEdited) {
      setCreateName(deriveNameFromUrl(v));
    }
  }, [nameManuallyEdited, deriveNameFromUrl]);

  const handleNameChange = useCallback((_e: unknown, v: string) => {
    setCreateName(v);
    setNameManuallyEdited(true);
  }, []);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchProjects = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    listProjects(50, 0)
      .then((data) => setProjects(data.items))
      .catch(() => {})
      .finally(() => { if (!silent) setLoading(false); });
  }, []);

  useEffect(() => {
    fetchProjects();
    pollRef.current = setInterval(() => fetchProjects(true), 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchProjects]);

  const handleCreate = useCallback(async () => {
    if (!createName.trim() || !createUrl.trim()) return;
    setCreating(true);
    try {
      const body: { name: string; repo_url: string; branch: string; scm_token?: string } = {
        name: createName.trim(),
        repo_url: createUrl.trim(),
        branch: createBranch.trim() || 'main',
      };
      if (createScmToken.trim()) body.scm_token = createScmToken.trim();
      await createProject(body);
      setShowCreate(false);
      setCreateName('');
      setCreateUrl('');
      setCreateBranch('main');
      setCreateScmToken('');
      setNameManuallyEdited(false);
      fetchProjects();
    } catch {
      // keep modal open
    } finally {
      setCreating(false);
    }
  }, [createName, createUrl, createBranch, createScmToken, fetchProjects]);

  const handleDelete = useCallback(async (proj: ProjectSummary) => {
    if (!confirm(`Delete project "${proj.name}"? This cannot be undone.`)) return;
    try {
      await deleteProject(proj.id);
      fetchProjects();
    } catch {
      alert('Failed to delete project.');
    }
  }, [fetchProjects]);

  const filtered = useMemo(() => {
    let items = [...projects];
    if (searchText.trim()) {
      const q = searchText.toLowerCase();
      items = items.filter(p =>
        p.name.toLowerCase().includes(q) ||
        p.repo_url.toLowerCase().includes(q) ||
        p.branch.toLowerCase().includes(q)
      );
    }
    items.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case 'name':
          cmp = a.name.localeCompare(b.name);
          break;
        case 'health_score':
          cmp = a.health_score - b.health_score;
          break;
        case 'total_violations':
          cmp = a.total_violations - b.total_violations;
          break;
        case 'scan_count':
          cmp = a.scan_count - b.scan_count;
          break;
        case 'last_scanned_at':
          cmp = (a.last_scanned_at ?? '').localeCompare(b.last_scanned_at ?? '');
          break;
      }
      return sortAsc ? cmp : -cmp;
    });
    return items;
  }, [projects, searchText, sortField, sortAsc]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortAsc(prev => !prev);
    } else {
      setSortField(field);
      setSortAsc(true);
    }
  };

  const SortIcon = sortAsc ? SortAmountUpIcon : SortAmountDownIcon;

  const sortableHeader = (label: string, field: SortField) => {
    const active = sortField === field;
    const ariaSortValue = active ? (sortAsc ? 'ascending' : 'descending') : undefined;
    return (
      <th
        role="columnheader"
        aria-sort={ariaSortValue}
        tabIndex={0}
        style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}
        onClick={() => handleSort(field)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleSort(field); } }}
      >
        {label}
        {active && (
          <SortIcon style={{ marginLeft: 4, fontSize: 12, opacity: 0.7 }} />
        )}
      </th>
    );
  };

  return (
    <PageLayout>
      <PageHeader title="Projects" />

      <Toolbar style={{ padding: '8px 24px' }}>
        <ToolbarContent>
          <ToolbarItem>
            <SearchInput
              placeholder="Filter by name or URL..."
              value={searchText}
              onChange={(_e, v) => setSearchText(v)}
              onClear={() => setSearchText('')}
              style={{ minWidth: 280 }}
            />
          </ToolbarItem>
          <ToolbarItem align={{ default: 'alignEnd' }} style={{ marginLeft: 16 }}>
            <Button variant="primary" icon={<PlusCircleIcon />} onClick={() => setShowCreate(true)}>
              Create project
            </Button>
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>

      <div style={{ padding: '0 24px 24px' }}>
        {loading ? (
          <div style={{ padding: 48, textAlign: 'center', opacity: 0.6 }}>Loading...</div>
        ) : filtered.length === 0 ? (
          projects.length === 0 ? (
            <EmptyState>
              <EmptyStateBody>
                No projects defined yet. Create one to get started.
              </EmptyStateBody>
            </EmptyState>
          ) : (
            <EmptyState>
              <EmptyStateBody>
                No projects match the current filter.
              </EmptyStateBody>
            </EmptyState>
          )
        ) : (
          <table className="pf-v6-c-table pf-m-grid-md" role="grid">
            <thead>
              <tr role="row">
                {sortableHeader('Name', 'name')}
                <th role="columnheader">Status</th>
                {sortableHeader('Health', 'health_score')}
                {sortableHeader('Violations', 'total_violations')}
                <th role="columnheader">Trend</th>
                {sortableHeader('Checks', 'scan_count')}
                {sortableHeader('Last Checked', 'last_scanned_at')}
                <th role="columnheader" style={{ width: 50 }} />
              </tr>
            </thead>
            <tbody>
              {filtered.map((proj) => (
                <tr
                  key={proj.id}
                  role="row"
                  tabIndex={0}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/projects/${proj.id}`)}
                  onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/projects/${proj.id}`); }}
                >
                  <td role="cell">
                    <div style={{ fontWeight: 600 }}>{proj.name}</div>
                    <div style={{ opacity: 0.6, fontFamily: 'var(--pf-t--global--font--family--mono)', fontSize: 12, marginTop: 2 }}>
                      {proj.repo_url} ({proj.branch})
                    </div>
                  </td>
                  <td role="cell">
                    {proj.active_operation ? (
                      proj.active_operation.status === 'awaiting_approval' ? (
                        <Label color="orange" isCompact>Action Required</Label>
                      ) : (
                        <Label color="blue" isCompact>
                          {proj.active_operation.scan_type === 'remediate' ? 'Remediating' : 'Checking'}
                        </Label>
                      )
                    ) : (
                      <StatusLabel lastScanned={proj.last_scanned_at} />
                    )}
                  </td>
                  <td role="cell"><HealthBadge score={proj.health_score} /></td>
                  <td role="cell">{proj.total_violations}</td>
                  <td role="cell"><TrendBadge trend={proj.violation_trend} /></td>
                  <td role="cell">{proj.scan_count}</td>
                  <td role="cell" style={{ opacity: 0.7, whiteSpace: 'nowrap' }}>
                    {proj.last_scanned_at ? timeAgo(proj.last_scanned_at) : 'Never'}
                  </td>
                  <td role="cell" onClick={(e) => e.stopPropagation()}>
                    <Dropdown
                      isOpen={openKebab === proj.id}
                      onSelect={() => setOpenKebab(null)}
                      onOpenChange={(open) => { if (!open) setOpenKebab(null); }}
                      toggle={(toggleRef) => (
                        <MenuToggle
                          ref={toggleRef}
                          variant="plain"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenKebab(prev => prev === proj.id ? null : proj.id);
                          }}
                          isExpanded={openKebab === proj.id}
                          aria-label="Actions"
                        >
                          <EllipsisVIcon />
                        </MenuToggle>
                      )}
                      popperProps={{ position: 'right' }}
                    >
                      <DropdownList>
                        <DropdownItem key="edit" onClick={() => navigate(`/projects/${proj.id}?tab=settings`)}>
                          Edit
                        </DropdownItem>
                        <DropdownItem key="delete" onClick={() => handleDelete(proj)} isDanger>
                          Delete
                        </DropdownItem>
                      </DropdownList>
                    </Dropdown>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Flex justifyContent={{ default: 'justifyContentFlexEnd' }} style={{ marginTop: 8, opacity: 0.6, fontSize: 13 }}>
          <FlexItem>{filtered.length} project{filtered.length !== 1 ? 's' : ''}</FlexItem>
        </Flex>
      </div>

      <Modal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        variant="small"
      >
        <ModalHeader title="Create Project" />
        <ModalBody>
          <Flex direction={{ default: 'column' }} gap={{ default: 'gapMd' }}>
            <FlexItem>
              <label htmlFor="proj-url" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Repository URL</label>
              <TextInput id="proj-url" value={createUrl} onChange={handleUrlChange} placeholder="https://github.com/org/repo.git" />
            </FlexItem>
            <FlexItem>
              <label htmlFor="proj-name" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Name</label>
              <TextInput id="proj-name" value={createName} onChange={handleNameChange} placeholder="My Ansible Project" />
            </FlexItem>
            <FlexItem>
              <label htmlFor="proj-branch" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>Branch</label>
              <TextInput id="proj-branch" value={createBranch} onChange={(_e, v) => setCreateBranch(v)} placeholder="main" />
            </FlexItem>
            <FlexItem>
              <label htmlFor="proj-scm-token" style={{ display: 'block', fontWeight: 600, marginBottom: 4 }}>SCM Token <span style={{ fontWeight: 400, opacity: 0.6 }}>(optional)</span></label>
              <TextInput id="proj-scm-token" type="password" value={createScmToken} onChange={(_e, v) => setCreateScmToken(v)} placeholder="GitHub PAT or App token" />
              <div style={{ fontSize: 12, marginTop: 4, opacity: 0.6 }}>
                Used for creating pull requests from remediation results.
              </div>
            </FlexItem>
          </Flex>
        </ModalBody>
        <ModalFooter>
          <Button variant="primary" onClick={handleCreate} isDisabled={creating || !createName.trim() || !createUrl.trim()}>
            {creating ? 'Creating...' : 'Create'}
          </Button>
          <Button variant="link" onClick={() => setShowCreate(false)}>Cancel</Button>
        </ModalFooter>
      </Modal>
    </PageLayout>
  );
}
