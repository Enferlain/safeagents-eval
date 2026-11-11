"""Security observation plugin. Native approval extension is not installed/enabled."""
from pathlib import Path

from .telemetry import Telemetry, register_observers


def register(ctx):
    from hermes_constants import get_hermes_home
    # The current trial belongs to default only. Never inherit its records across profiles.
    if Path(get_hermes_home()).resolve() != (Path.home() / '.hermes').resolve():
        return
    directory = Path.home() / 'Projects' / 'safeagents-eval' / 'trial' / 'private-observation'
    register_observers(ctx, Telemetry(directory))
