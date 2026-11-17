/** Multi-tag typeahead using the full scan's rule IDs as its option source. */
import { useMemo, useRef, useState } from 'react';
import { ButtonIcon, Select, Flex, Tag, TagGroup } from '@backstage/ui';
import { RiCloseLine } from '@remixicon/react';

export interface RuleFilterInputProps {
  /** Bare rule IDs present in the current scan (autocomplete source). */
  options: string[];
  /** Currently selected bare rule IDs (OR filter). */
  selected: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  id?: string;
}

export function RuleFilterInput({
  options,
  selected,
  onChange,
  placeholder = 'Filter by rule ID…',
  id = 'rule-filter',
}: RuleFilterInputProps) {
  const [inputValue, setInputValue] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const selectOptions = useMemo(() => {
    const query = inputValue.trim().toLowerCase();
    const filtered = options
      .filter((rule) => rule.toLowerCase().includes(query))
      .sort(
        (a, b) =>
          Number(selected.includes(a)) - Number(selected.includes(b)) ||
          a.localeCompare(b),
      )
      .map((rule) => ({ id: rule, label: rule }));
    return filtered.length
      ? filtered
      : [
          {
            id: '__no_results__',
            label: query
              ? `No rules match "${inputValue.trim()}"`
              : 'No rules in this scan',
            disabled: true,
          },
        ];
  }, [options, selected, inputValue]);

  const focusTrigger = () => {
    filterRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
  };

  if (options.length === 0 && selected.length === 0) return null;

  return (
    <div
      className="apme-rule-filter"
      ref={filterRef}
      onKeyDownCapture={(event) => {
        if (event.key === 'Backspace' && !inputValue && selected.length > 0) {
          event.preventDefault();
          onChange(selected.slice(0, -1));
        }
      }}
    >
      <span className="apme-rule-filter__label">Rule</span>
      <Select
        className="apme-rule-filter-toggle"
        id={id}
        aria-label="Filter by rule ID"
        placeholder={placeholder}
        options={selectOptions}
        selectionMode="multiple"
        value={selected}
        isOpen={isOpen}
        onOpenChange={setIsOpen}
        onChange={(keys) => {
          onChange(keys.map(String));
          setInputValue('');
          setIsOpen(false);
        }}
        search={{
          inputValue,
          onInputChange: setInputValue,
          filter: () => true,
          placeholder,
        }}
      />
      {selected.length > 0 && (
        <Flex gap="2" align="center">
          <TagGroup
            aria-label="Selected rule filters"
            onRemove={(keys) => {
              onChange(selected.filter((rule) => !keys.has(rule)));
              setInputValue('');
              focusTrigger();
            }}
          >
            {selected.map((rule) => (
              <Tag key={rule} id={rule}>
                {rule}
              </Tag>
            ))}
          </TagGroup>
          <ButtonIcon
            variant="tertiary"
            aria-label="Clear rule filters"
            icon={<RiCloseLine />}
            onPress={() => {
              onChange([]);
              setInputValue('');
              focusTrigger();
            }}
          />
        </Flex>
      )}
    </div>
  );
}
