"""Health-log plugin registration."""

from .tools import LOG_WEIGHT_SCHEMA, log_weight


def register(ctx) -> None:
    """Expose the tightly-scoped health logging tool."""
    ctx.register_tool(
        name="log_weight",
        toolset="health_log",
        schema=LOG_WEIGHT_SCHEMA,
        handler=log_weight,
    )
