import { describe, it, expect } from 'vitest';
import {
  normalizeFindingNodeType,
  nodeTypeLabel,
  nodeTypeLabelColor,
  orderPresentNodeTypes,
} from '../remediation/nodeType';

describe('normalizeFindingNodeType', () => {
  it('keeps wire values', () => {
    expect(normalizeFindingNodeType('task')).toBe('task');
    expect(normalizeFindingNodeType('Block')).toBe('block');
  });

  it('maps empty to other', () => {
    expect(normalizeFindingNodeType('')).toBe('other');
    expect(normalizeFindingNodeType(null)).toBe('other');
    expect(normalizeFindingNodeType(undefined)).toBe('other');
  });
});

describe('nodeTypeLabel', () => {
  it('uses known labels and title-cases unknowns', () => {
    expect(nodeTypeLabel('task')).toBe('Task');
    expect(nodeTypeLabel('vars_file')).toBe('Vars');
    expect(nodeTypeLabel('filter_plugin')).toBe('Filter Plugin');
  });
});

describe('orderPresentNodeTypes', () => {
  it('orders known kinds then extras', () => {
    expect(orderPresentNodeTypes(['role', 'task', 'module', 'block'])).toEqual([
      'task',
      'block',
      'role',
      'module',
    ]);
  });
});

describe('nodeTypeLabelColor', () => {
  it('maps common kinds to distinct PatternFly colors', () => {
    expect(nodeTypeLabelColor('task')).toBe('blue');
    expect(nodeTypeLabelColor('play')).toBe('orange');
    expect(nodeTypeLabelColor('playbook')).toBe('red');
    expect(nodeTypeLabelColor('role')).toBe('purple');
    expect(nodeTypeLabelColor('other')).toBe('grey');
  });
});
