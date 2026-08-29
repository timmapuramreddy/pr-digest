import importlib
import sys


def test_module_imports_without_env_or_token(monkeypatch):
    monkeypatch.delenv("PR_SCAN_TOKEN", raising=False)
    sys.modules.pop("digest", None)
    importlib.invalidate_caches()
    import digest  # noqa: F401 — must not touch os.environ at import time
