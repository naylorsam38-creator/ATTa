"""Test setup: every test runs against a throwaway APP_BUILDER_ROOT, never /srv/app-builder."""
import os, sys, tempfile
from pathlib import Path

os.environ["APP_BUILDER_ROOT"] = tempfile.mkdtemp(prefix="atta-test-root-")
os.environ.setdefault("APP_BUILDER_NETWORK_IDLE_GRACE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "04-deployment"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
