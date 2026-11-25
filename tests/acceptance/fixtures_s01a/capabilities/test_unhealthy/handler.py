def noop(payload, ctx):
    return {}


def healthcheck():
    return {"healthy": False, "why": "simulated dependency outage"}


def verify(payload, output):
    return True
