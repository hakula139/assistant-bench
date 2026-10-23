import { useId } from 'react';

import { KeptTail } from '@/components/site/Term';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Kbd } from '@/components/ui/kbd';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { defectTerms } from '@/lib/glossary';
import { defects, type Defect } from '@/lib/report';

type KeysFor<T extends readonly unknown[]> = { readonly [K in keyof T]: string };

/** The key that toggles each defect, in `defects` order. */
export type DefectKeys = KeysFor<typeof defects>;

interface DefectPickerProps {
  side: 'A' | 'B';
  /** The defects tagged on this output, in `defects` order. */
  tagged: readonly Defect[];
  /** The key that toggles each defect on this output, in `defects` order. */
  keys: DefectKeys;
  onToggle: (defect: Defect) => void;
}

/** One output's Issues button and the issues it can be tagged with, each with what it means. */
export function DefectPicker({ side, tagged, keys, onToggle }: DefectPickerProps) {
  const id = useId();
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          Issues
          {tagged.length > 0 && (
            <>
              {' '}
              <Badge variant="secondary">{tagged.length}</Badge>
            </>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" collisionPadding={16} aria-label={`Issues in output ${side}`}>
        <ul className="flex flex-col gap-3">
          {defects.map((defect, index) => {
            const key = `${id}-${defect}`;
            const { term, definition } = defectTerms[defect];
            return (
              <li key={defect} className="flex items-start gap-2">
                <Checkbox
                  id={key}
                  checked={tagged.includes(defect)}
                  onCheckedChange={() => {
                    onToggle(defect);
                  }}
                  aria-describedby={`${key}-definition`}
                  aria-keyshortcuts={keys[index]}
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
                <Kbd aria-hidden="true">{keys[index]}</Kbd>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
