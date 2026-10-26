# Make order notifications transactional and retryable

The application in `/app` persists orders and sends their current values directly to a separate receiver when dispatch runs. It has no durable notification queue or delivery acknowledgements, so repeated dispatch sends old orders again and notification bytes are reconstructed on every attempt.

Implement the placement and dispatch contracts in `README.md`. An order and its one immutable notification must commit together. Identical repeated placement is idempotent, and changed cents conflict. Dispatch attempts pending records in insertion order, keeps failed deliveries pending, continues to later records, and commits each successful delivered flag independently. A receiver can accept before the local delivered update fails, so retries must reuse its key and exact stored payload bytes.

Complete the public API and persisted format described in `README.md`, keeping the placement and dispatch signatures and CLI. Database failures must surface, while expected `DeliveryError` failures belong in `DispatchResult`. Databases created by the starter need no migration support. Add focused regression tests for these boundaries. Use only Python 3.14's standard library.
