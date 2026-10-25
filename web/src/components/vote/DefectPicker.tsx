import { useId } from 'react';

import { KeptTail } from '@/components/site/Term';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Kbd } from '@/components/ui/kbd';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { defectTerms } from '@/lib/glossary';
import type { Defect } from '@/lib/report';

interface DefectPickerProps {
  side: 'A' | 'B';
  /** The family's defects, in its order. */
  defects: readonly Defect[];
  tagged: readonly Defect[];
  disabled: boolean;
  /** Keys for the first defects, in `defects` order. */
  keys: readonly string[];
  onToggle: (defect: Defect) => void;
}

export function DefectPicker({
  side,
  defects,
  tagged,
  disabled,
  keys,
  onToggle,
}: DefectPickerProps) {
  const id = useId();

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" disabled={disabled}>
          Issues
          {tagged.length > 0 && (
            <>
              {' '}
              <Badge variant="secondary">{tagged.length}</Badge>
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        collisionPadding={16}
        aria-label={`Issues in output ${side}`}
        className="max-w-[calc(100vw-2rem)]"
      >
        <ul className="flex flex-col gap-3">
          {defects.map((defect, index) => {
            const key = `${id}-${defect}`;
            const shortcut = keys.at(index);
            const { term, definition } = defectTerms[defect];

            return (
              <li key={defect} className="flex items-start gap-2">
                <Checkbox
                  id={key}
                  checked={tagged.includes(defect)}
                  disabled={disabled}
                  onCheckedChange={() => {
                    onToggle(defect);
                  }}
                  aria-describedby={`${key}-definition`}
                  aria-keyshortcuts={shortcut}
                  className="mt-0.5"
                />
                <div className="flex flex-1 flex-col gap-0.5">
                  <label htmlFor={key} className="font-medium">
                    {term}
                  </label>
                  <p id={`${key}-definition`} className="text-muted-foreground">
                    <KeptTail text={definition} />
                  </p>
                </div>
                {shortcut !== undefined && <Kbd aria-hidden="true">{shortcut}</Kbd>}
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
