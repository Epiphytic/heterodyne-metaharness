"""Standalone pure presentation V1. No transport, source acquisition, auth or sends."""

from .classification import classify
from .rendering import render, render_card_pair

__all__ = ["classify", "render", "render_card_pair"]
