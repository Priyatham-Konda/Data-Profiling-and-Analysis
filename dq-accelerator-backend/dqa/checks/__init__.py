"""Check implementations, one module per dimension.

Importing this package registers every check. The rule loader validates
against that registry, so a YAML rule naming a check that does not exist
fails loudly at load rather than silently at runtime.

To add a check: write the function, decorate it with @cell_check("name") or
the class with @stateful_check("name"), import it here, and add a rule to
rules/default_pack.yaml. The executor never changes.

`integrity` covers relationships within one dataset. Cross-dataset
integrity is still outstanding; see dqa/config.py.
"""
from . import accuracy  # noqa: F401
from . import completeness  # noqa: F401
from . import consistency  # noqa: F401
from . import integrity  # noqa: F401
from . import timeliness  # noqa: F401
from . import uniqueness  # noqa: F401
from . import validity  # noqa: F401

__all__ = [
    "accuracy",
    "completeness",
    "consistency",
    "integrity",
    "timeliness",
    "uniqueness",
    "validity",
]
