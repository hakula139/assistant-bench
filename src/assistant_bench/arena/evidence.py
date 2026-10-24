"""Replay immutable ledger events without consulting mutable projections."""

from collections import defaultdict

from assistant_bench_arena.ledger_models import Actor, Event, EventTarget, Receipt


def replay(
    actors: tuple[Actor, ...],
    receipts: tuple[Receipt, ...],
    events: tuple[Event, ...],
    targets: tuple[EventTarget, ...],
) -> tuple[Receipt, ...]:
    """Recover selected ordinary and repeat claims after withdrawal and moderation."""
    observations = {row.seq: row for row in receipts}
    commands = {event.seq: event for event in events}
    identities = {actor.id for actor in actors}

    if (
        len(observations) != len(receipts)
        or len(commands) != len(events)
        or len(identities) != len(actors)
    ):
        raise ValueError('Export identities must be unique.')

    by_event: defaultdict[int, list[EventTarget]] = defaultdict(list)
    for target in targets:
        if target.event_seq not in commands:
            raise ValueError('An event target has no captured event.')

        if target.receipt_seq is not None and target.receipt_seq not in observations:
            raise ValueError('An event target has no captured receipt.')

        by_event[target.event_seq].append(target)

    selected: dict[str, int] = {}
    withdrawn: set[int] = set()
    superseded: set[int] = set()
    excluded: dict[int, int] = {}

    for event in sorted(events, key=lambda row: row.seq):
        event_targets = sorted(by_event[event.seq], key=lambda row: row.ordinal)
        if len({row.ordinal for row in event_targets}) != len(event_targets):
            raise ValueError('An event repeats a target ordinal.')

        for target in event_targets:
            receipt = target.receipt_seq
            claim = target.claim_id
            match target.transition:
                case 'withdraw' | 'supersede':
                    if receipt is not None:
                        (
                            withdrawn if target.transition == 'withdraw' else superseded
                        ).add(receipt)
                        if claim is not None and selected.get(claim) == receipt:
                            del selected[claim]

                case 'exclude':
                    if receipt is None:
                        raise ValueError('An exclusion has no receipt.')

                    excluded[receipt] = event.seq

                case 'restore':
                    if receipt is None or excluded.get(receipt) != target.reference_seq:
                        raise ValueError(
                            'A restoration does not reference its exclusion.'
                        )

                    del excluded[receipt]

                case 'revoke':
                    pass

                case _:
                    raise ValueError(
                        'The export contains an unsupported event transition.'
                    )

        if event.kind == 'accept':
            accepted = observations.get(event.seq)
            if accepted is None:
                raise ValueError('An acceptance has no native receipt.')

            selected[accepted.claim_id] = accepted.seq

    active = {
        seq
        for seq in selected.values()
        if seq not in withdrawn and seq not in superseded and seq not in excluded
    }
    for observation in receipts:
        if observation.actor_id not in identities:
            raise ValueError('A receipt names an uncaptured actor.')

        if (
            observation.seq not in commands
            or commands[observation.seq].kind != 'accept'
        ):
            raise ValueError('A native receipt has no acceptance event.')

    return tuple(
        observations[seq]
        for seq in sorted(active)
        if observations[seq].repeat_of is None or observations[seq].repeat_of in active
    )
