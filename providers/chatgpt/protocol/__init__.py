"""OpenAI / ChatGPT platform OAuth registration protocol (in-process)."""

from .flow import ChatGPTRegisterError, RegistrationResult, register_one

__all__ = ["ChatGPTRegisterError", "RegistrationResult", "register_one"]
