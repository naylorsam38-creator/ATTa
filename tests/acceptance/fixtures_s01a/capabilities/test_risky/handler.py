def run(payload, ctx):
    open(payload["marker"], "w").write("executed")
    return {}


def healthcheck():
    return True


def verify(payload, output):
    return True
