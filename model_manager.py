"""
Global Model Manager Entrypoint for !05_agy_agent.
Exposes GlobalModelManager, ModelDashboardView and all interactive components
from the underlying agy_agent production module.
"""
import sys
import os

_submodule_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agy_agent")
if _submodule_dir not in sys.path:
    sys.path.insert(0, _submodule_dir)

import importlib.util

_target_file = os.path.join(_submodule_dir, "model_manager.py")
_spec = importlib.util.spec_from_file_location("agy_agent_model_manager_impl", _target_file)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({k: getattr(_mod, k) for k in dir(_mod) if not k.startswith("__")})

if __name__ == "__main__":
    print("=== Global Model Manager Status ===")
    roles = GlobalModelManager.get_all_roles()
    for rk, cfg in roles.items():
        st = "ON" if cfg.get("enabled", True) else "OFF"
        print(f"[{rk.upper()}] ({st}) Engine: {cfg.get('engine')} | Model: {cfg.get('model')}")
