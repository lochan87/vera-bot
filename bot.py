"""Single-module submission interface for the magicpin Vera challenge.

The challenge's offline contract is compose(category, merchant, trigger, customer).
This file intentionally contains no external dependencies and is deterministic.
"""
from app import compose

__all__ = ["compose"]
