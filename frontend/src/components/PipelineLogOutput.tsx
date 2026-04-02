import { useMemo, useRef, useState } from 'react';
import {
  Button,
  Modal,
  ModalHeader,
  ModalBody,
  Tab,
  Tabs,
  TabTitleText,
} from '@patternfly/react-core';
import {
  AngleDownIcon,
  AngleRightIcon,
  AngleDoubleUpIcon,
  AngleDoubleDownIcon,
} from '@patternfly/react-icons';
import { PageDetails, PageDetail } from '@ansible/ansible-ui-framework';

export interface PipelineLogEntry {
  id: number;
  message: string;
  phase: string;
  progress: number;
  level: number;
}

function levelClass(level: number): string {
  if (level >= 40) return 'error';
  if (level >= 30) return 'medium';
  if (level >= 20) return 'info';
  return 'debug';
}

function levelLabel(level: number): string {
  if (level >= 40) return 'ERROR';
  if (level >= 30) return 'WARN';
  if (level >= 20) return 'INFO';
  return 'DEBUG';
}

function groupByPhase(entries: PipelineLogEntry[]): Map<string, PipelineLogEntry[]> {
  const map = new Map<string, PipelineLogEntry[]>();
  for (const e of entries) {
    const key = e.phase || '(unknown)';
    const arr = map.get(key) ?? [];
    arr.push(e);
    map.set(key, arr);
  }
  return map;
}

interface PipelineLogOutputProps {
  logs: PipelineLogEntry[];
  expanded?: boolean;
}

function LogDetailModal({ isOpen, onClose, entry }: { isOpen: boolean; onClose: () => void; entry: PipelineLogEntry }) {
  const [activeTab, setActiveTab] = useState(0);

  return (
    <Modal isOpen={isOpen} onClose={onClose} aria-label="Log entry details" width="75%">
      <ModalHeader title="Log Entry Details" />
      <ModalBody>
        <Tabs
          aria-label="Log detail tabs"
          activeKey={activeTab}
          onSelect={(_e, key) => setActiveTab(key as number)}
        >
          <Tab eventKey={0} title={<TabTitleText>Details</TabTitleText>} aria-label="Details tab">
            <PageDetails>
              <PageDetail label="Phase">{entry.phase}</PageDetail>
              <PageDetail label="Level">
                <span className={`apme-severity ${levelClass(entry.level)}`}>{levelLabel(entry.level)}</span>
              </PageDetail>
              <PageDetail label="Progress">{entry.progress}%</PageDetail>
              <PageDetail label="Message">{entry.message}</PageDetail>
            </PageDetails>
          </Tab>
          <Tab eventKey={1} title={<TabTitleText>Data</TabTitleText>} aria-label="Data tab">
            <div className="apme-modal-diff">
              <pre>{JSON.stringify(entry, null, 2)}</pre>
            </div>
          </Tab>
        </Tabs>
      </ModalBody>
    </Modal>
  );
}

export function PipelineLogOutput({ logs, expanded }: PipelineLogOutputProps) {
  const [sectionOpen, setSectionOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [allCollapsed, setAllCollapsed] = useState(false);
  const [selectedEntry, setSelectedEntry] = useState<PipelineLogEntry | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(() => groupByPhase(logs), [logs]);

  const togglePhase = (phase: string) => {
    setCollapsed(prev => ({ ...prev, [phase]: !prev[phase] }));
  };

  const expandAll = () => {
    const next: Record<string, boolean> = {};
    for (const key of groups.keys()) next[key] = false;
    setCollapsed(next);
    setAllCollapsed(false);
  };

  const collapseAll = () => {
    const next: Record<string, boolean> = {};
    for (const key of groups.keys()) next[key] = true;
    setCollapsed(next);
    setAllCollapsed(true);
  };

  const toggleAll = () => {
    if (allCollapsed) expandAll(); else collapseAll();
  };

  const isCollapsed = (phase: string) => collapsed[phase] === true;

  if (logs.length === 0) return null;

  return (
    <div className={`apme-pipeline-log-section${sectionOpen ? '' : ' apme-pipeline-log-collapsed'}${sectionOpen && expanded ? ' apme-pipeline-log-expanded' : ''}`}>
      <div className="apme-output-controls">
        <div className="apme-output-controls-left">
          <Button
            variant="plain"
            onClick={() => setSectionOpen(prev => !prev)}
            aria-label={sectionOpen ? 'Hide pipeline log' : 'Show pipeline log'}
            icon={sectionOpen ? <AngleDownIcon /> : <AngleRightIcon />}
            size="sm"
          />
          <span
            style={{ fontSize: 13, fontWeight: 600, paddingLeft: 4, cursor: 'pointer' }}
            role="button"
            tabIndex={0}
            onClick={() => setSectionOpen(prev => !prev)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSectionOpen(prev => !prev); }}
          >
            Pipeline Log ({logs.length})
          </span>
        </div>
        {sectionOpen && (
          <div className="apme-output-controls-right">
            <Button
              variant="plain"
              onClick={toggleAll}
              aria-label={allCollapsed ? 'Expand all phases' : 'Collapse all phases'}
              icon={allCollapsed ? <AngleRightIcon /> : <AngleDownIcon />}
              size="sm"
            />
            <Button
              variant="plain"
              onClick={() => {
                scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
              }}
              icon={<AngleDoubleUpIcon />}
              aria-label="Scroll to top"
              size="sm"
            />
            <Button
              variant="plain"
              onClick={() => {
                const el = scrollRef.current;
                if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
              }}
              icon={<AngleDoubleDownIcon />}
              aria-label="Scroll to bottom"
              size="sm"
            />
          </div>
        )}
      </div>

      {sectionOpen && <div className="apme-output-scroll" ref={scrollRef}>
        <div className="apme-output-grid">
          {Array.from(groups.entries()).map(([phase, entries]) => (
            <div className="apme-output-file-group" key={phase}>
              <div
                className="apme-output-row apme-output-row-header"
                role="button"
                tabIndex={0}
                onClick={() => togglePhase(phase)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') togglePhase(phase); }}
              >
                <span className="apme-output-gutter">
                  {isCollapsed(phase) ? <AngleRightIcon /> : <AngleDownIcon />}
                </span>
                <span className="apme-output-content apme-output-file-line">
                  <span className="apme-output-file-path">{phase}</span>
                  <span className="apme-output-file-meta">
                    {entries.length} message{entries.length !== 1 ? 's' : ''}
                  </span>
                </span>
              </div>

              {isCollapsed(phase) && (
                <div className="apme-output-row apme-output-row-ellipsis">
                  <span className="apme-output-gutter" />
                  <span className="apme-output-content">...</span>
                </div>
              )}

              {!isCollapsed(phase) && entries.map((entry) => (
                <div
                  className="apme-output-row apme-output-row-item"
                  key={entry.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedEntry(entry)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedEntry(entry); }}
                >
                  <span className="apme-output-gutter apme-output-line-num">
                    {entry.progress > 0 ? `${entry.progress}%` : ''}
                  </span>
                  <span className="apme-output-content apme-output-violation-line">
                    <span className={`apme-severity ${levelClass(entry.level)}`} style={{ minWidth: 44 }}>
                      {levelLabel(entry.level)}
                    </span>
                    <span className="apme-output-violation-msg">
                      {entry.message}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>}

      {selectedEntry && (
        <LogDetailModal
          isOpen={!!selectedEntry}
          onClose={() => setSelectedEntry(null)}
          entry={selectedEntry}
        />
      )}
    </div>
  );
}
