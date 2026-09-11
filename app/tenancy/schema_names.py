"""tenant key -> Postgres schema name.

One function, used everywhere. It exists mainly so the name is validated in
exactly one place: this string gets interpolated into SQL (you cannot bind a
schema name as a parameter), so an unvalidated value here would be injection.
"""

import re

# t_ + lowercase letters, digits, underscore. Nothing else may reach SQL.
_SAFE = re.compile(r"^t_[a-z0-9_]{1,50}$")


def schema_for(tenant_key: str) -> str:
    name = "t_" + str(tenant_key).replace("-", "").lower()
    if not _SAFE.match(name):
        raise ValueError(f"unsafe schema name derived from {tenant_key!r}: {name!r}")
    return name
