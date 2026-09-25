import sqlite3
from pathlib import Path
DB=Path("/opt/atta/data/atta.db")
def connect(): DB.parent.mkdir(parents=True,exist_ok=True); return sqlite3.connect(DB)
def init():
 db=connect(); db.executescript("""PRAGMA foreign_keys=ON; CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,package TEXT,status TEXT,stage TEXT,created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"""); db.commit(); db.close()
