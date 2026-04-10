import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_external_binaries():
    with patch("shutil.which", side_effect=lambda x: {
        "devcontainer": "/usr/bin/devcontainer",
        "code": "/usr/bin/code",
    }.get(x)):
        yield
