from pathlib import Path
def repair(root):
 fixes=[]; app=Path(root)/"app"; app.mkdir(exist_ok=True)
 s=app/"styles.css"
 if not s.exists(): s.write_text("body{font-family:system-ui}"); fixes.append("Generated styles.css")
 return fixes
