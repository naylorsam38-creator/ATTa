"""TEST FIXTURE: a local notes connector (provider adapter) reading a JSON file in the task workspace."""
import json


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def search(payload, ctx):
    notes = _load(payload["path"])
    q = payload["query"].lower()
    hits = [n for n in notes if q in (n.get("title", "") + " " + n.get("body", "")).lower()]
    return {"matches": hits, "count": len(hits)}


def read(payload, ctx):
    for n in _load(payload["path"]):
        if n.get("id") == payload["note_id"]:
            return {"note": n}
    raise KeyError("note not found")


def create(payload, ctx):
    notes = _load(payload["path"])
    nid = "n%d" % (len(notes) + 1)
    notes.append({"id": nid, "title": payload["title"], "body": payload["body"]})
    with open(payload["path"], "w", encoding="utf-8") as f:
        json.dump(notes, f)
    return {"created": nid}


def send(payload, ctx):
    return {"sent": True}


def healthcheck():
    return {"healthy": True, "detail": "notes connector ready"}
