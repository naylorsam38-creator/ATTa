# Change: Front Door no longer depends on Claude (2026-09-24)

Before: the Front Door page asked its questions through `window.claude`, which only
exists when the page is opened inside Claude. On the server it did nothing.

Now:
- `02-front-door/front-door.html` sends each question to the server:
  `POST /api/front-door/model`. No `window.claude` anywhere in the page.
  Saved progress uses the browser's own storage.
- `04-deployment/gateway.py` handles that endpoint and calls the Anthropic API
  with the server's `ANTHROPIC_API_KEY` (plain HTTPS, no extra package needed).

Settings (server .env, all optional except the key):
- `ANTHROPIC_API_KEY`                  required, same key the self-healer uses
- `APP_BUILDER_FRONTDOOR_MODEL`        default `claude-haiku-4-5-20251001` (cheapest)
- `APP_BUILDER_FRONTDOOR_PER_MIN`      default 20 questions per person per minute
- `APP_BUILDER_FRONTDOOR_PER_DAY`      default 300 questions per person per day

With no key set, the page shows: "The model isn't set up on this server yet."
