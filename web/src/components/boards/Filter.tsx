import { useId } from 'react';

import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';

interface FilterProps {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
}

/** One labelled filter in the row above a board's charts. */
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
