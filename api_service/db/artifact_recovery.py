"""Owner classification shared by recovery admission and its database index."""

from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement

DEPLOYMENT_SUBJECT_ID = "00000000-0000-0000-0000-000000000000"
MACHINE_PRINCIPALS = ("operator", "system", "agent_workflow", "agent_runtime")
MACHINE_PREFIXES = ("service:", "workflow:", "system:")
# Match Python str.strip, including whitespace-only machine suffixes.
PRINCIPAL_WHITESPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

RECOVERY_MACHINE = 0
RECOVERY_RESERVED_SCOPE = 1
RECOVERY_REQUIRED = 2


def is_artifact_machine_principal(raw: object) -> bool:
    value = str(raw or "").strip()
    return value in MACHINE_PRINCIPALS or any(
        value.startswith(prefix) and bool(value[len(prefix) :].strip())
        for prefix in MACHINE_PREFIXES
    )


def artifact_recovery_owner_class(principal: ColumnElement) -> ColumnElement[int]:
    """Classify ungranted owners without changing their retained provenance.

    Constants are inlined so PostgreSQL generic prepared plans can match the
    expression index. Changing this expression requires rebuilding that index
    in a migration. The reserved scope remains separate because its exemption
    depends on the current deployment configuration, never a stored grant.
    """

    def fixed(value):
        return literal(value, literal_execute=True)

    def stripped(value):
        return func.ltrim(
            func.rtrim(value, fixed(PRINCIPAL_WHITESPACE)),
            fixed(PRINCIPAL_WHITESPACE),
        )

    creator = func.coalesce(principal, fixed(""))
    normalized = stripped(creator)
    machine = or_(
        normalized.in_([fixed(value) for value in MACHINE_PRINCIPALS]),
        *(
            and_(
                func.substr(normalized, fixed(1), fixed(len(prefix))) == fixed(prefix),
                stripped(func.substr(normalized, fixed(len(prefix) + 1))) != fixed(""),
            )
            for prefix in MACHINE_PREFIXES
        ),
    )
    return case(
        (machine, fixed(RECOVERY_MACHINE)),
        (
            creator.in_(
                [fixed(DEPLOYMENT_SUBJECT_ID), fixed(f"user:{DEPLOYMENT_SUBJECT_ID}")]
            ),
            fixed(RECOVERY_RESERVED_SCOPE),
        ),
        else_=fixed(RECOVERY_REQUIRED),
    )
