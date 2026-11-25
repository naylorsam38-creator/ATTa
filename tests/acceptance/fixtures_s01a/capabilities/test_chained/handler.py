def noop(payload, ctx):
    return {"ok": True}


def healthcheck():
    return True


def verify(payload, output):
    return True
