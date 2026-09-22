"""typesafe.ai / jev console magic-link registration (in-process)."""

from .flow import TypesafeRegisterError, TypesafeResult, probe_console, register_one

__all__ = ["TypesafeRegisterError", "TypesafeResult", "probe_console", "register_one"]
