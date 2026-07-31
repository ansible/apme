/**
 * Multi-tag typeahead for filtering review lists by rule ID.
 *
 * Options come from the full present set for the scan (not the currently
 * narrowed rows), so users can add more rules after the list shrinks.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Label,
  LabelGroup,
  MenuToggle,
  type MenuToggleElement,
  Select,
  SelectList,
  SelectOption,
  type SelectOptionProps,
  TextInputGroup,
  TextInputGroupMain,
  TextInputGroupUtilities,
} from '@patternfly/react-core';
import { TimesIcon } from '@patternfly/react-icons';

const NO_RESULTS = '__no_results__';

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
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [focusedItemIndex, setFocusedItemIndex] = useState<number | null>(null);
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  const selectOptions: SelectOptionProps[] = useMemo(() => {
    const q = inputValue.trim().toLowerCase();
    let filtered = options;
    if (q) {
      filtered = options.filter((r) => r.toLowerCase().includes(q));
    }
    // Prefer unselected rules first so the menu stays useful with many tags.
    filtered = [...filtered].sort((a, b) => {
      const aSel = selected.includes(a) ? 1 : 0;
      const bSel = selected.includes(b) ? 1 : 0;
      if (aSel !== bSel) return aSel - bSel;
      return a.localeCompare(b);
    });
    if (filtered.length === 0) {
      return [
        {
          isAriaDisabled: true,
          children: q
            ? `No rules match "${inputValue.trim()}"`
            : 'No rules in this scan',
          value: NO_RESULTS,
        },
      ];
    }
    return filtered.map((r) => ({
      value: r,
      children: r,
      isSelected: selected.includes(r),
    }));
  }, [options, selected, inputValue]);

  useEffect(() => {
    if (inputValue && !isOpen) {
      setIsOpen(true);
    }
  }, [inputValue, isOpen]);

  const createItemId = (value: string) =>
    `${id}-option-${value.replace(/[^a-zA-Z0-9_-]/g, '-')}`;

  const resetFocus = () => {
    setFocusedItemIndex(null);
    setActiveItemId(null);
  };

  const closeMenu = () => {
    setIsOpen(false);
    resetFocus();
  };

  const toggleRule = (value: string) => {
    if (!value || value === NO_RESULTS) return;
    onChange(
      selected.includes(value)
        ? selected.filter((s) => s !== value)
        : [...selected, value],
    );
    setInputValue('');
    // Close after pick — multi-add is via reopening / typing / clicking a RuleId.
    closeMenu();
  };

  const handleMenuArrowKeys = (key: string) => {
    if (!isOpen) setIsOpen(true);
    if (selectOptions.every((o) => o.isAriaDisabled || o.isDisabled)) return;

    let indexToFocus = 0;
    if (key === 'ArrowUp') {
      if (focusedItemIndex === null || focusedItemIndex === 0) {
        indexToFocus = selectOptions.length - 1;
      } else {
        indexToFocus = focusedItemIndex - 1;
      }
      while (selectOptions[indexToFocus]?.isAriaDisabled || selectOptions[indexToFocus]?.isDisabled) {
        indexToFocus--;
        if (indexToFocus < 0) indexToFocus = selectOptions.length - 1;
      }
    } else {
      if (
        focusedItemIndex === null ||
        focusedItemIndex === selectOptions.length - 1
      ) {
        indexToFocus = 0;
      } else {
        indexToFocus = focusedItemIndex + 1;
      }
      while (selectOptions[indexToFocus]?.isAriaDisabled || selectOptions[indexToFocus]?.isDisabled) {
        indexToFocus++;
        if (indexToFocus >= selectOptions.length) indexToFocus = 0;
      }
    }
    setFocusedItemIndex(indexToFocus);
    const focused = selectOptions[indexToFocus];
    if (focused?.value) setActiveItemId(createItemId(String(focused.value)));
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const focused =
      focusedItemIndex !== null ? selectOptions[focusedItemIndex] : null;
    switch (event.key) {
      case 'Enter':
        if (
          isOpen &&
          focused?.value &&
          focused.value !== NO_RESULTS &&
          !focused.isAriaDisabled
        ) {
          event.preventDefault();
          toggleRule(String(focused.value));
        } else if (!isOpen) {
          setIsOpen(true);
        }
        break;
      case 'Backspace':
        if (!inputValue && selected.length > 0) {
          onChange(selected.slice(0, -1));
        }
        break;
      case 'Escape':
        if (isOpen) {
          event.stopPropagation();
          closeMenu();
        }
        break;
      case 'ArrowUp':
      case 'ArrowDown':
        event.preventDefault();
        handleMenuArrowKeys(event.key);
        break;
      default:
        break;
    }
  };

  const toggle = (toggleRef: React.Ref<MenuToggleElement>) => (
    <MenuToggle
      variant="typeahead"
      aria-label="Filter by rule ID"
      onClick={() => {
        setIsOpen((o) => !o);
        textInputRef.current?.focus();
      }}
      innerRef={toggleRef}
      isExpanded={isOpen}
      className="apme-rule-filter-toggle"
    >
      <TextInputGroup isPlain>
        <TextInputGroupMain
          value={inputValue}
          onClick={() => {
            if (!isOpen) setIsOpen(true);
          }}
          onChange={(_e, value) => {
            setInputValue(value);
            resetFocus();
          }}
          onKeyDown={onInputKeyDown}
          id={`${id}-input`}
          autoComplete="off"
          innerRef={textInputRef}
          placeholder={selected.length === 0 ? placeholder : ''}
          {...(activeItemId ? { 'aria-activedescendant': activeItemId } : {})}
          role="combobox"
          isExpanded={isOpen}
          aria-controls={`${id}-listbox`}
        >
          <LabelGroup aria-label="Selected rule filters" numLabels={12}>
            {selected.map((rule) => (
              <Label
                key={rule}
                variant="outline"
                onClose={(ev) => {
                  ev.stopPropagation();
                  toggleRule(rule);
                }}
              >
                {rule}
              </Label>
            ))}
          </LabelGroup>
        </TextInputGroupMain>
        <TextInputGroupUtilities
          {...(selected.length === 0 ? { style: { display: 'none' } } : {})}
        >
          <Button
            variant="plain"
            onClick={() => {
              onChange([]);
              setInputValue('');
              resetFocus();
              textInputRef.current?.focus();
            }}
            aria-label="Clear rule filters"
            icon={<TimesIcon />}
          />
        </TextInputGroupUtilities>
      </TextInputGroup>
    </MenuToggle>
  );

  if (options.length === 0 && selected.length === 0) {
    return null;
  }

  return (
    <div className="apme-rule-filter">
      <span className="apme-rule-filter__label">Rule</span>
      <Select
        id={id}
        isOpen={isOpen}
        selected={selected}
        onSelect={(_event, selection) => toggleRule(String(selection))}
        onOpenChange={(open) => {
          if (!open) closeMenu();
        }}
        toggle={toggle}
        variant="typeahead"
      >
        <SelectList isAriaMultiselectable id={`${id}-listbox`}>
          {selectOptions.map((option, index) => (
            <SelectOption
              key={String(option.value ?? option.children)}
              isFocused={focusedItemIndex === index}
              id={createItemId(String(option.value))}
              value={option.value}
              isAriaDisabled={option.isAriaDisabled}
              isSelected={option.isSelected}
            >
              {option.children}
            </SelectOption>
          ))}
        </SelectList>
      </Select>
    </div>
  );
}
