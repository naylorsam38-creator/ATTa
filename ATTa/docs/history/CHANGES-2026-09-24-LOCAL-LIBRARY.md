# Change: library is authoritative (2026-09-24)

File: `04-deployment/pipeline.py`, function `library()`.

Before: every build ran `git fetch` for each app already in the library.
Now: if an app's folder is already in the library, it is used exactly as it is.
No fetch, no network call, ever. Only apps not yet in the library are cloned.

Why: the library copy is the one repairs are saved into. It must never be
overwritten or depend on the network once it exists.

Result code in `state/library-results.json`: `USING_LOCAL_LIBRARY`
(replaces `FETCHED_PRESERVE_LOCAL` and `FETCH_FAILED_USING_LOCAL`).

To get a newer upstream version of an app: delete its folder in `library/`
and the next build will clone it fresh.
