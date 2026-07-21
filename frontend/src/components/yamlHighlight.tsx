/**
 * Per-line YAML highlighting for Current/Proposed panes (Prism).
 */

import { useMemo } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-yaml';

const MAX_CACHE_ENTRIES = 500;
const cache = new Map<string, string>();

function cacheSet(line: string, html: string): void {
  if (cache.size >= MAX_CACHE_ENTRIES) {
    cache.clear();
  }
  cache.set(line, html);
}

/** Highlight a single YAML line; returns HTML string with Prism token spans. */
export function highlightYamlLine(line: string): string {
  if (!line) return '';
  const hit = cache.get(line);
  if (hit !== undefined) return hit;
  try {
    const html = Prism.highlight(line, Prism.languages.yaml ?? {}, 'yaml');
    cacheSet(line, html);
    return html;
  } catch {
    cacheSet(line, '');
    return '';
  }
}

/** Render one YAML source line with Prism tokens (or plain text on failure). */
export function YamlLine({ text }: { text: string | null }) {
  const content = text ?? '';
  const html = useMemo(
    () => (content ? highlightYamlLine(content) : ''),
    [content],
  );

  if (!content) {
    return <span className="apme-yaml-code">{'\u00A0'}</span>;
  }
  if (!html) {
    return <span className="apme-yaml-code">{content}</span>;
  }
  return (
    <span
      className="apme-yaml-code"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
