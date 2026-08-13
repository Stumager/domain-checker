"""Config injection for modules that run off the request thread.

Background workers (scan pipeline, Maps pollers, proxy checks) have no Flask
app context, so current_app is unavailable to them. The app factory pushes the
values they need into a module-level dict at startup instead.
"""

import logging

logger = logging.getLogger(__name__)


def apply_config(store: dict, config: dict, source: str = "", warn_unknown: bool = True):
    """Copy known, non-empty values from *config* into *store*.

    Keys the module does not declare are logged rather than silently accepted —
    an unknown key is nearly always a typo in the factory, and without this it
    would look like the setting simply had no effect.

    Pass ``warn_unknown=False`` where one config dict is deliberately shared by
    two modules that each take a different subset of it.
    """
    for key, value in (config or {}).items():
        if key not in store:
            if warn_unknown:
                logger.warning("Ignoring unknown setting %r for %s", key, source or "service")
            continue
        if value in (None, "", []):
            continue
        store[key] = value
