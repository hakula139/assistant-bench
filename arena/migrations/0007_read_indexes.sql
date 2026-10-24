CREATE INDEX numerics_page ON evaluation_numerics(evaluation_id, json_array(section, row_key));
CREATE INDEX receipts_repeat_due ON vote_receipts(actor_id, family, seq, recorded_at)
WHERE repeat_of IS NULL;
CREATE INDEX claims_actor_page ON claims(family, canonical_actor, id)
WHERE merged_into IS NULL;
