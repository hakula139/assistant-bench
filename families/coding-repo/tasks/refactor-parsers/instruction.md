`records/parsers.py` in `/app` repeats the same splitting, field-count, empty-field, and type-conversion logic in `parse_user`, `parse_order`, and `parse_product`. Refactor it so that this shared logic lives in a private module-level helper named `_parse_fields`, which all three parsers call:

- Split the line only inside `_parse_fields`.
- Leave no `try` statements in the three public parsers.
- Preserve the observable behavior exactly: the returned objects, the exception types and attributes, the error messages, and which inputs are accepted or rejected.

Keep `python3 -m unittest` passing.
