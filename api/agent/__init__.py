"""Agent loop + product-side tool extras.

Importing this package side-effect-registers the product-specific tools
(Project*, EnvSet) into the core tool registry. The agent loop is then
able to expose them to the model without any additional wiring.
"""

from __future__ import annotations

from . import extras  # noqa: F401  — side-effect register_extras()
