"""Validator abstraction: ScanContext and Validator protocol."""

from .ansible import AnsibleValidator
from .base import ScanContext, Validator
from .opa import OpaValidator

__all__ = ["ScanContext", "Validator", "OpaValidator", "AnsibleValidator"]
