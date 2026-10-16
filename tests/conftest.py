"""Use ordinary workspace directories for tests on restricted Windows hosts."""
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[1] / 'output' / 'test-fixtures' / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path
