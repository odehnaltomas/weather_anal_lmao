from pathlib import Path

import pytest

from chmi_downloader.validation import ValidationError, Validator


def test_validate_file_requires_non_empty_file(tmp_path):
    path = tmp_path / 'empty.bin'
    path.write_bytes(b'')
    with pytest.raises(ValidationError):
        Validator.validate_file(path)


def test_validate_file_checks_suffix(tmp_path):
    path = tmp_path / 'sample.csv'
    path.write_bytes(b'col1,col2\n1,2\n')
    with pytest.raises(ValidationError):
        Validator.validate_file(path, required_suffix='.hdf5')
