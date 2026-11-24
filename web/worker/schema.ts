import type { Generated } from 'kysely';

import type { Choice, PairView } from '@/lib/arena';
import type { Category } from '@/lib/categories';

/**
 * Vote-store tables and views across `web/migrations/*.sql`.
 *
 * `schema.test.ts` parses this interface directly to check table, view, column, type, nullability,
 * and default parity against the migrated D1 schema.
 */
export interface Database {
  publications: {
    id: string;
    family: string;
    display_name: string;
    aspect: string | null;
    render: Generated<PairView['render']>;
    defects: string;
    reference: string;
    systems: string;
    case_count: number;
    output_count: number;
    created_at: number;
    target: Generated<'production' | 'private'>;
  };
  cases: {
    publication_id: string;
    case_key: string;
    category: Category;
    instruction: string;
    checklist: string;
    split: Generated<'public' | 'held-out' | 'private'>;
    source: 'public' | 'synthetic' | 'transcript' | 'held-out' | null;
  };
  outputs: {
    publication_id: string;
    output_key: string;
    case_key: string;
    system: string;
    fingerprint: string;
    verdict: 'pass' | 'fail' | 'ungraded';
    errored: 0 | 1;
    length: number | null;
    content_digest: string;
    content: string | null;
  };
  active: {
    family: string;
    publication_id: string;
    activated_at: number;
  };
  actors: {
    id: string;
    created_at: number;
    ip_tag: string | null;
  };
  pairs: {
    id: string;
    actor_id: string;
    family: string;
    publication_id: string;
    case_key: string;
    a_output: string;
    b_output: string;
    issued_at: number;
  };
  votes: {
    id: Generated<number>;
    pair_id: string;
    actor_id: string;
    family: string;
    publication_id: string;
    case_key: string;
    a_output: string;
    b_output: string;
    a_system: string;
    b_system: string;
    choice: Choice;
    defects: string;
    elapsed_ms: number;
    recorded_at: number;
    ip_tag: string | null;
    exclusion: string | null;
  };
  pair_vote_counts: {
    family: string;
    low_system: string;
    high_system: string;
    votes: number;
  };
  publication_pair_cases: {
    publication_id: string;
    low_system: string;
    high_system: string;
    case_keys: string;
  };
  batch_pairs: {
    id: string;
    actor_id: string;
    family: string;
    publication_id: string;
    case_key: string;
    a_output: string;
    b_output: string;
    issued_at: number;
    position: number;
  };
  pending_pairs: {
    id: string;
    actor_id: string;
    family: string;
    publication_id: string;
    case_key: string;
    a_output: string;
    b_output: string;
    issued_at: number;
    position: number;
    batched: 0 | 1;
  };
}
