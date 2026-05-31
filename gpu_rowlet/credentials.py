from __future__ import annotations


class CredentialError(RuntimeError):
    pass


def get_password(service: str, key: str) -> str | None:
    try:
        import keyring
    except ImportError as exc:
        raise CredentialError("keyring is not installed. Run: python -m pip install -r requirements.txt") from exc
    try:
        return keyring.get_password(service, key)
    except Exception as exc:
        raise CredentialError(f"Unable to read Windows credential '{service}/{key}': {exc}") from exc


def set_password(service: str, key: str, password: str) -> None:
    try:
        import keyring
    except ImportError as exc:
        raise CredentialError("keyring is not installed. Run: python -m pip install -r requirements.txt") from exc
    try:
        keyring.set_password(service, key, password)
    except Exception as exc:
        raise CredentialError(f"Unable to save Windows credential '{service}/{key}': {exc}") from exc


def delete_password(service: str, key: str) -> None:
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.delete_password(service, key)
    except Exception:
        return
