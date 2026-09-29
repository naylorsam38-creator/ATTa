# v119 — runtime journey qualification gate

## Acceptance rule

v119 changes stage 6 from “the browser loaded the page” to “the browser proved a user journey”. A web application without an enabled journey contract is NOT_QUALIFIED.

### Login journeys

A login journey requires a real qualification test account. The username/password may be supplied directly in the isolated qualification recipe or through `env:NAME` references. The browser fills the real fields, submits the form, and verifies the post-login destination and/or expected element/text. Missing credentials, missing login fields, a failed login, or a missing post-login expectation fails qualification.

### Non-login web journeys

A smoke journey must perform an explicit browser interaction and verify an expected URL, selector, or text result. A page load alone is never accepted.

### AI boundary

The self-healer may add or repair a journey recipe, but it cannot mark the app passed. The same watcher must execute the journey and produce the verdict.

### Catalogue boundary

The pipeline writes `state/qualified_catalogue.json` from apps that passed the complete gate. The Front Door receives only category/qualified-URL data from that manifest; internal application names remain out of the user-facing catalogue. If the manifest is empty, the Front Door remains usable but has no eligible foundation to match.

A PARTIALLY_QUALIFIED build is not handed to Coolify. Coolify hand-off occurs only for a fully `QUALIFIED` build.

## Verification

- v119 runtime journey tests: 4/4 passed.
- v117 Coolify/provisioning regression suite: 31/31 passed.
- Front Door JavaScript syntax check: passed using the extracted script block with Node.js.
- Python compilation of deployment sources: passed.
- Full historical unittest discovery was attempted but did not complete within five minutes; the run stalled in the existing v114.1 hardening suite. The affected test passes individually.
- Docker is not installed in this execution environment, so no real Docker/AWS catalogue run was claimed.
