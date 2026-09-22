"""
Top-level Discord Bot Launcher & Gateway Entrypoint for !05_agy_agent.
Proxies to agy_agent.bot to ensure direct invocation capability from the root workspace.
"""
import sys
import os

_submodule_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agy_agent")
if _submodule_dir not in sys.path:
    sys.path.insert(0, _submodule_dir)

from agy_agent.bot import *

if __name__ == "__main__":
    main()
