"""Integrity and lightweight format checks for downloaded CHMI files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional


class ValidationError(Exception):
    """Raised when a downloaded or archived file fails an integrity check."""

    pass


class Validator:
    """Stateless validation helpers shared by download and verify commands."""

    @staticmethod
    def sha256(path: Path) -> str:
        """Calculate SHA-256 without loading a potentially large file at once."""
        if not path.exists():
            raise ValidationError('File does not exist')
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            # One-megabyte chunks keep memory usage stable for large datasets.
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def validate_file(path: Path, expected_size: Optional[int] = None, expected_sha256: Optional[str] = None, required_suffix: Optional[str] = None) -> Dict[str, Any]:
        """Validate presence, size, suffix, and optional recorded checksum."""
        if not path.exists():
            raise ValidationError('File does not exist')

        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise ValidationError('File is empty')
        if expected_size is not None and size_bytes != expected_size:
            raise ValidationError('File size mismatch')
        if required_suffix is not None and path.suffix.lower() != required_suffix.lower():
            raise ValidationError('File extension mismatch')

        sha256 = Validator.sha256(path)
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise ValidationError('SHA-256 mismatch')

        return {'sha256': sha256, 'size_bytes': size_bytes}

    @staticmethod
    def basic_format_check(path: Path, expected_format: Optional[str] = None) -> bool:
        """Reject obvious HTML/error payloads masquerading as data files."""
        if expected_format is None:
            return True
        if expected_format.lower() == 'hdf5':
            try:
                header = path.read_bytes()[:8]
            except Exception:
                return False
            # Every HDF5 file begins with this fixed eight-byte signature.
            return header == b'\x89HDF\r\n\x1a\n'
        if expected_format.lower() == 'csv':
            # Staging files have a .part suffix; inspect the content instead
            # of trusting the filename. This is only a basic format check.
            prefix = path.read_bytes()[:4096].lstrip()
            if prefix.lower().startswith((b'<html', b'<!doctype html')):
                return False
            return any(delimiter in prefix for delimiter in (b',', b';', b'\t'))
        if expected_format.lower() == 'json':
            try:
                prefix = path.read_bytes()[:1024].lstrip()
            except Exception:
                return False
            # Full JSON parsing is deferred; this catches HTML and empty files
            # cheaply during collection.
            return prefix.startswith((b'{', b'['))
        return True
