"""TEST FIXTURE: a handler that tries to cause a side effect AFTER it should have been stopped."""
import os
import time


def sleep_and_mark(payload, ctx):
    if os.fork() == 0:          # detached grandchild, survives its parent unless the supervisor kills it
        os.setsid()
        if os.fork() == 0:
            time.sleep(payload["delay"])
            with open(payload["marker"], "w") as f:
                f.write("late side effect")
            os._exit(0)
        os._exit(0)
    time.sleep(payload["sleep"])
    return {"slept": payload["sleep"]}


def noop(payload, ctx):
    return {"ok": True, "n": payload.get("n", 0)}


def healthcheck():
    return True


def verify_any(payload, output):
    return True


def record_url(payload, ctx):
    return {"recorded": payload["url"]}     # never opens a connection
