"""TEST FIXTURE: the 'external' effect happens BEFORE the handler finishes, so killing it leaves the outcome uncertain."""
import time


def send(payload, ctx):
    with open(payload["ledger"], "a") as f:
        f.write(f"{ctx.execution_id} {payload['message']}\n")
    time.sleep(payload["sleep"])
    return {"sent": True}


def reconcile(payload, ctx):
    try:
        lines = open(payload["ledger"]).read().splitlines()
    except FileNotFoundError:
        lines = []
    return {"applied": any(ln.startswith(ctx.execution_id) for ln in lines)}


def healthcheck():
    return True


def verify(payload, output):
    return output.get("sent") is True
