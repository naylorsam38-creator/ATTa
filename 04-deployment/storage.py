from pathlib import Path
DATA=Path("/opt/atta/data"); DATA.mkdir(parents=True,exist_ok=True)
SECRET=DATA/"secret_key"
def load_secret():
 import secrets
 if not SECRET.exists(): SECRET.write_text(secrets.token_hex(32)); SECRET.chmod(0o600)
 return SECRET.read_text().strip()
