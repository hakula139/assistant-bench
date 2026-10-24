Rename the customer lookup and the customer name field across the `shop` project in `/app`:

- `shop.repository.get_customer` becomes `find_customer`. Keep `get_customer` as a deprecated alias that emits a `DeprecationWarning` and returns the same result as `find_customer`.
- The `Customer.name` field becomes `Customer.display_name`.
- Update every caller and every test. The dictionaries returned by `shop.api` keep their `name` key, since clients depend on it.

Make sure `python3 -m unittest` passes, then commit all of your changes in a single commit whose subject follows Conventional Commits with the `refactor` type.
