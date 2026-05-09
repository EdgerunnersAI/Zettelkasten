"""PageIndex_Rag retired pending v2 redesign.

Was a direct legacy data-access layer that bypassed RLS — incompatible with
the v2 workspace model.
"""
from __future__ import annotations


def __getattr__(name: str):
    raise NotImplementedError(
        f"PageIndex_Rag.data_access.{name} is retired pending v2 redesign"
    )
