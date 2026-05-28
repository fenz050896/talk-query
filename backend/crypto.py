import os
from cryptography.fernet import Fernet

_KEY_PATH = os.path.join(os.path.dirname(__file__), ".fernet_key")
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Load or generate the Fernet key from file."""
    global _fernet
    if _fernet is not None:
        return _fernet

    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(_KEY_PATH, "wb") as f:
            f.write(key)
        print(f"[crypto] Generated new Fernet key at {_KEY_PATH}")

    _fernet = Fernet(key)
    return _fernet


def encrypt_password(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
