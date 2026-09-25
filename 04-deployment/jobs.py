from .database import connect
from pathlib import Path
UPLOADS=Path("/opt/atta/uploads"); UPLOADS.mkdir(parents=True,exist_ok=True)
def create(name):
 db=connect(); cur=db.execute("INSERT INTO jobs(package,status,stage) VALUES(?,?,?)",(name,"queued","queued")); db.commit(); jid=cur.lastrowid; db.close(); return jid
