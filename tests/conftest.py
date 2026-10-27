from collections.abc import Iterator

import pytest
from openhost_test_harness import OpenhostStack


@pytest.fixture(scope="session")
def stack() -> Iterator[OpenhostStack]:
    """Builds the Dockerfile and deploys the app through the real router; returns once the app is running."""
    # The image build pulls several large upstream images plus apt and npm installs (~2 min in CI).
    with OpenhostStack(deploy_timeout=900) as s:
        yield s
