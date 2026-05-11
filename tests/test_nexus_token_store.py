from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from website.experimental_features.nexus.service.token_store import ProviderTokenStore
from website.experimental_features.nexus.source_ingest.common.models import NexusProvider


def test_delete_account_returns_true_when_account_is_gone() -> None:
    client = MagicMock()
    store = ProviderTokenStore(
        client=client,
        encryption_key="7TgtMgeR5dMTnXxW6ULICwhf66A1VpzwuNFuIBqmoe4=",
        core_repo=MagicMock(),
    )
    store.get_account = MagicMock(return_value=None)

    deleted = store.delete_account(uuid4(), NexusProvider.GITHUB)

    assert deleted is True


def test_delete_account_returns_false_when_account_still_exists() -> None:
    client = MagicMock()
    store = ProviderTokenStore(
        client=client,
        encryption_key="7TgtMgeR5dMTnXxW6ULICwhf66A1VpzwuNFuIBqmoe4=",
        core_repo=MagicMock(),
    )
    store.get_account = MagicMock(return_value=object())

    deleted = store.delete_account(uuid4(), NexusProvider.REDDIT)

    assert deleted is False
