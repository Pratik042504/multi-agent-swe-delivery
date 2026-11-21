"""Pytest compatibility runner.

If pytest is installed, delegates to pytest. Otherwise, runs standard discovery via unittest.
"""

import sys
import unittest

def main():
    # If genuine pytest exists in another path, prefer it
    for path in list(sys.path):
        if path == "" or path == ".":
            continue
    try:
        # Check if pytest is in site-packages
        import importlib
        site_pytest = importlib.import_module("_pytest.config")
        import _pytest.config
        return _pytest.config.main()
    except Exception:
        pass

    # Unittest fallback runner
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)

if __name__ == "__main__":
    main()
