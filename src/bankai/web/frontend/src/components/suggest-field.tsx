import { useEffect, useId, useRef, useState, type ReactNode, type Ref } from 'react';
import { Search } from 'lucide-react';
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/zon/ui/input-group';
import { cn } from '@/zon/lib/cn';

export interface Suggestion {
  /** What a pick puts into the field. */
  value: string;
  label: ReactNode;
  /** Shown faint beside it: why it matched, how much there is. */
  hint?: ReactNode;
}

/**
 * A search field that suggests as one types: a free-text field still (Enter searches what is
 * typed), with a list under it once two letters are in -- arrows move through it, Enter or a
 * click takes one, Escape closes it. zon's Combobox accepts listed values only, so not that.
 */
export function SuggestField({
  value,
  onValueChange,
  load,
  placeholder,
  label,
  inputRef,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  load: (query: string) => Promise<Suggestion[]>;
  placeholder?: string;
  label: string;
  inputRef?: Ref<HTMLInputElement>;
  className?: string;
}) {
  const id = useId();
  const [items, setItems] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Only what was typed asks for suggestions: a pick or a restored search does not open the list.
  const typed = useRef(false);
  const asked = useRef(0);

  useEffect(() => {
    if (!typed.current) return;
    const query = value.trim();
    if (query.length < 2) {
      setItems([]);
      setOpen(false);
      return;
    }
    const ask = ++asked.current;
    const timer = window.setTimeout(() => {
      load(query)
        .then((found) => {
          if (ask !== asked.current) return;
          setItems(found);
          setActive(-1);
          setOpen(found.length > 0);
        })
        .catch(() => undefined);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [value, load]);

  function pick(item: Suggestion) {
    typed.current = false;
    asked.current++;
    onValueChange(item.value);
    setOpen(false);
  }

  return (
    <div className={cn('relative', className)}>
      <InputGroup>
        <InputGroupAddon>
          <Search aria-hidden='true' />
        </InputGroupAddon>
        <InputGroupInput
          ref={inputRef}
          value={value}
          role='combobox'
          aria-label={label}
          aria-autocomplete='list'
          aria-expanded={open}
          aria-controls={id}
          aria-activedescendant={open && active >= 0 ? `${id}-${active}` : undefined}
          autoComplete='off'
          placeholder={placeholder}
          onChange={(event) => {
            typed.current = true;
            onValueChange(event.target.value);
          }}
          onBlur={() => setOpen(false)}
          onFocus={() => items.length > 0 && typed.current && setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' && items.length) {
              event.preventDefault();
              setOpen(true);
              setActive((at) => (at + 1) % items.length);
            } else if (event.key === 'ArrowUp' && open) {
              event.preventDefault();
              setActive((at) => (at <= 0 ? items.length - 1 : at - 1));
            } else if (event.key === 'Enter' && open && active >= 0) {
              event.preventDefault();
              pick(items[active]);
            } else if (event.key === 'Escape' && open) {
              event.preventDefault();
              setOpen(false);
            }
          }}
        />
      </InputGroup>
      {open && (
        <ul
          id={id}
          role='listbox'
          aria-label={label + ': suggestions'}
          className='absolute inset-x-0 top-full z-30 mt-1 max-h-80 overflow-auto rounded-control surface-container p-1 shadow-overlay'
        >
          {items.map((item, index) => (
            <li
              key={item.value + index}
              id={`${id}-${index}`}
              role='option'
              aria-selected={index === active}
              data-active={index === active ? '' : undefined}
              // Before the field's blur closes the list.
              onMouseDown={(event) => {
                event.preventDefault();
                pick(item);
              }}
              onMouseEnter={() => setActive(index)}
              className='flex cursor-pointer items-baseline gap-2 rounded-[calc(var(--radius-control)-4px)] px-2.5 py-1.5 text-sm text-foreground data-active:bg-control-surface'
            >
              <span className='min-w-0 flex-1 truncate'>{item.label}</span>
              {item.hint && <span className='max-w-[50%] shrink-0 truncate text-xs text-muted-foreground'>{item.hint}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
