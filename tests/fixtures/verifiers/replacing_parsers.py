def replacement(line: str) -> str:
    return 'changed'


def parse_user(line: str) -> str:
    globals()['parse_user'] = replacement
    return 'original'


def parse_order(line: str) -> str:
    return 'order'


def parse_product(line: str) -> str:
    return 'product'
