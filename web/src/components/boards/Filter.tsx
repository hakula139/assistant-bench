import { useId } from 'react';

import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import type { RoundLink } from '@/lib/reports';

interface FilterProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}

export function Filter({ label, value, options, onChange }: FilterProps) {
  const id = useId();

  return (
    <div className="flex items-center gap-2 text-sm">
      <label htmlFor={id} className="text-muted-foreground">
        {label}
      </label>
      <NativeSelect
        id={id}
        size="sm"
        className="min-w-36"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      >
        {options.map((option) => (
          <NativeSelectOption key={option.value} value={option.value}>
            {option.label}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
  );
}

/**
 * Moves between a family's round pages, given newest first, and keeps the query string so the
 * board's other filters hold.
 */
export function RoundFilter({ rounds, current }: { rounds: RoundLink[]; current: string }) {
  return (
    <Filter
      label="Round"
      value={current}
      onChange={(value) => {
        const link = rounds.find((item) => item.round === value);
        if (link !== undefined) {
          window.location.assign(`${link.href}${window.location.search}`);
        }
      }}
      options={rounds.map(({ round }, index) => ({
        value: round,
        label: index === 0 ? `${round} (latest)` : round,
      }))}
    />
  );
}
