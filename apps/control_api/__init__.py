"""Project-owned Web control plane API."""

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from apps.control_api.app import create_app

        return create_app
    raise AttributeError(name)
