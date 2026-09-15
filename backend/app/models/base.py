from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every per-tenant table.

    RULE: no model in this project ever passes `schema=` to __table_args__.

    An unqualified table name is resolved by Postgres against `search_path`,
    which the tenant gate sets per request. That is what makes isolation
    structural rather than a filter somebody can forget to write.
    """
