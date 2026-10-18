"""reserved.py — account names that belong to ATTa's own processes, never to a person.

v115: trust comes from WHERE a job came from (its recorded origin), never from the name on it. These
names are refused as accounts (accounts.py), an existing account with one of them is disabled on the next
`accounts.py init` and can never log in (accounts.get() hides it), and ADM refuses a web job that names one.

One list, read by accounts.py and by ADM (deployd/adm/authz.py loads this file by path, so it works in
the source tree and in the live code folder alike). Add a name here if a new internal process gets one.
"""

RESERVED_NAMES = frozenset({
    "system",      # pipeline: a bundle placed in the protected local inbox on the server
    "deployctl",   # ADM: `deployctl deploy` run by root
    "incoming",    # ADM: a zip placed in ADM's protected incoming/ folder
    "local",       # gateway: the APP_BUILDER_AUTH_DISABLED test identity
    "root", "atta", "adm", "deployd", "pipeline", "gateway", "watcher", "coolify", "anonymous",
})

# Where a build or deploy job came from. Anything else, or nothing, is refused.
ORIGIN_WEB = "web"      # created by the gateway for a logged-in account
ORIGIN_LOCAL = "local"  # created on the server itself, from a root-only folder
ORIGINS = (ORIGIN_WEB, ORIGIN_LOCAL)


def is_reserved(name) -> bool:
    return isinstance(name, str) and name.strip().lower() in RESERVED_NAMES
