"""Service-manager control (ADR 0001 §3.2). The backend comes from host config, never sys.platform."""

import re

# A unit name that can't be read as an option, a path or a glob.
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]*\.(?:service|target|timer|socket)")
