"""Cross-platform credential encryption.

On Windows: uses DPAPI (CryptProtectData / CryptUnprotectData), binding the
encrypted blob to the current Windows user account — nobody else can decrypt it.

On macOS / Linux: no equivalent OS API is available without extra dependencies,
so dpapi_encrypt / dpapi_decrypt are identity functions and security relies on
the credential file being readable only by the owner (chmod 600, applied by
secure_credential_file() after every write).
"""

import sys
import os


def secure_credential_file(path: str) -> None:
    """Restrict a credential file to owner-read/write only (macOS / Linux)."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


if sys.platform == "win32":
    import ctypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def dpapi_encrypt(data: bytes) -> bytes:
        blob_in = _DATA_BLOB(len(data), ctypes.cast(
            ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    def dpapi_decrypt(data: bytes) -> bytes:
        blob_in = _DATA_BLOB(len(data), ctypes.cast(
            ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

else:
    def dpapi_encrypt(data: bytes) -> bytes:
        return data

    def dpapi_decrypt(data: bytes) -> bytes:
        return data
