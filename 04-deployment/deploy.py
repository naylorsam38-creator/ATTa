from pathlib import Path
import shutil
ROOT=Path("/opt/atta"); REL=ROOT/"releases"; CUR=ROOT/"current"
def activate(tag):
 REL.mkdir(parents=True,exist_ok=True); tgt=REL/tag
 if CUR.exists() or CUR.is_symlink(): CUR.unlink()
 CUR.symlink_to(tgt)
