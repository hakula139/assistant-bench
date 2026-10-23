import { useId } from 'react';

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

/** One output's Issues button and the issues it can be tagged with. */
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
        <ul className="space-y-2">
          {defects.map((defect, index) => {
            const key = `${id}-${defect}`;
            return (
              <li key={defect} className="flex items-center gap-2">
                <Checkbox
                  id={key}
                  checked={tagged.includes(defect)}
                  onCheckedChange={() => {
                    onToggle(defect);
                  }}
                  aria-keyshortcuts={keys[index]}
                />
                <label htmlFor={key}>{defectTerms[defect].term}</label>
                <Kbd aria-hidden="true" className="ml-auto">
                  {keys[index]}
                </Kbd>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
