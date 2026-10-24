CREATE INDEX actor_quota_owner ON actor_resolution(actor_id, canonical_actor);
CREATE INDEX quota_owner_sources ON actor_resolution(canonical_actor, actor_id);
