/**
 * ContentGraph node kinds on findings (wire ``node_type``).
 *
 * Wire/API values match engine ``NodeType`` (``task``, ``block``, ``play``, …);
 * empty/missing remains ``""`` on the wire. For Kind filters and pills, call
 * ``normalizeFindingNodeType``, which maps empty/missing to the presentation
 * sentinel ``other`` (not an engine enum member).
 */

/** Wire or presentation kind string (not a closed enum). */
export type FindingNodeType = string;

/** Preferred pill order; any other present kinds append alphabetically. */
export const NODE_TYPE_ORDER: string[] = [
  'task',
  'block',
  'play',
  'handler',
  'playbook',
  'taskfile',
  'role',
  'vars_file',
  'collection',
  'module',
  'rulebook',
  'ruleset',
  'other',
];

const NODE_TYPE_LABELS: Record<string, string> = {
  task: 'Task',
  block: 'Block',
  play: 'Play',
  handler: 'Handler',
  playbook: 'Playbook',
  taskfile: 'Task file',
  role: 'Role',
  vars_file: 'Vars',
  collection: 'Collection',
  module: 'Module',
  rulebook: 'Rulebook',
  ruleset: 'Ruleset',
  other: 'Other',
};

/** Normalize wire ``node_type`` for filters (empty → other). */
export function normalizeFindingNodeType(
  raw: string | undefined | null,
): FindingNodeType {
  const t = (raw || '').trim().toLowerCase();
  return t || 'other';
}

export function nodeTypeLabel(nodeType: FindingNodeType): string {
  if (NODE_TYPE_LABELS[nodeType]) return NODE_TYPE_LABELS[nodeType];
  return nodeType
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * PatternFly Label ``color`` for a node-type pill.
 *
 * Roughly aligned with graph viz hues (task/playbook blue/red family, etc.).
 */
export function nodeTypeLabelColor(
  nodeType: FindingNodeType,
):
  | 'red'
  | 'orange'
  | 'yellow'
  | 'blue'
  | 'grey'
  | 'green'
  | 'teal'
  | 'purple' {
  switch (normalizeFindingNodeType(nodeType)) {
    case 'playbook':
      return 'red';
    case 'play':
      return 'orange';
    case 'block':
      return 'yellow';
    case 'task':
      return 'blue';
    case 'taskfile':
      return 'teal';
    case 'handler':
    case 'module':
      return 'green';
    case 'role':
    case 'collection':
      return 'purple';
    default:
      return 'grey';
  }
}

/** Order present kinds: known order first, then any extras A–Z. */
export function orderPresentNodeTypes(present: Iterable<string>): string[] {
  const set = new Set(present);
  const ordered = NODE_TYPE_ORDER.filter((t) => set.has(t));
  const extras = [...set]
    .filter((t) => !NODE_TYPE_ORDER.includes(t))
    .sort((a, b) => a.localeCompare(b));
  return [...ordered, ...extras];
}
