"""Shared package — config, model registry, providers, audit, cache.

Business layer depends only on these abstractions; never on concrete model SDKs
or absolute paths (instruct #5, #60, #65, #66).
"""
