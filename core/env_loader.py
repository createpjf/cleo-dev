"""
core/env_loader.py
Minimal .env file loader — no external dependencies.
Loads KEY=VALUE pairs into os.environ at startup.
"""

import os


# Backwards compatibility: map old SWARM_* env vars to new CLEO_* names.
# If user has SWARM_GATEWAY_PORT set but not CLEO_GATEWAY_PORT, honour the old one.
_ENV_COMPAT = {
    "SWARM_GATEWAY_PORT": "CLEO_GATEWAY_PORT",
    "SWARM_GATEWAY_TOKEN": "CLEO_GATEWAY_TOKEN",
    "SWARM_REPO": "CLEO_REPO",
    "SWARM_INSTALL_DIR": "CLEO_INSTALL_DIR",
}


def load_dotenv(path: str = ""):
    """
    Load KEY=VALUE pairs from a .env file into os.environ.
    - Skips blank lines and comments (lines starting with #)
    - Strips surrounding quotes (' or ") from values
    - Uses os.environ.setdefault so real env vars take precedence
    - Defaults to project root .env if no path given
    """
    if not path:
        # Default: project root (parent of core/)
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".env")
    if not os.path.exists(path):
        return

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)

    # Backwards compatibility: honour old SWARM_* vars → CLEO_* aliases
    for old_key, new_key in _ENV_COMPAT.items():
        if old_key in os.environ and new_key not in os.environ:
            os.environ[new_key] = os.environ[old_key]
