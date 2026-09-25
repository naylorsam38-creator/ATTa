import json, zipfile
REQUIRED=["atta.json","Dockerfile","app/index.html"]
def validate(zp):
 e=[]
 with zipfile.ZipFile(zp) as z:
  n=set(z.namelist())
  [e.append(f"Missing {r}") for r in REQUIRED if r not in n]
  if "atta.json" in n:
   d=json.loads(z.read("atta.json"))
   if d.get("package_spec")!="atta-v1": e.append("Unsupported package_spec")
 return e
