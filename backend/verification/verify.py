"""Production-grade verifier entry point (correction 8):

    .venv/bin/python -I -P backend/verification/verify.py <command> ...

Loads ONLY this directory's modules (protected, root-owned) plus the protected virtualenv — never backend/__init__.py
or any other worker-editable module. `python -m backend.verification` remains for convenience but imports the
backend package and is therefore not the trusted path."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.argv[0] = "verify.py"
runpy.run_path(os.path.join(HERE, "__main__.py"), run_name="__main__")
