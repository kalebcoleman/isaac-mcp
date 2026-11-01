# Keep policy/core importable for real OpenUSD CPU tests without importing Kit.
import importlib.util

if importlib.util.find_spec("carb"):
    from .extension import Extension
