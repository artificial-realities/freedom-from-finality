"""Minimal test runner (pytest-compatible tests, no pytest dependency).

    python -m tests.run            # run everything
    python -m tests.run test_story # one module

Async test functions are awaited; a function that raises fails.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import sys
import traceback


def main(argv: list[str]) -> int:
    import tests
    names = argv or [m.name for m in pkgutil.iter_modules(tests.__path__) if m.name.startswith("test_")]
    passed = failed = 0
    for name in names:
        module = importlib.import_module(f"tests.{name}")
        for attr in dir(module):
            if not attr.startswith("test_"):
                continue
            fn = getattr(module, attr)
            if not callable(fn):
                continue
            try:
                result = fn()
                if inspect.isawaitable(result):
                    asyncio.run(result)
                passed += 1
                print(f"ok   {name}.{attr}")
            except Exception:
                failed += 1
                print(f"FAIL {name}.{attr}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
