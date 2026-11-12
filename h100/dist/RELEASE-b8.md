# AIRexploit H100 pack, b8

Adds to b7: **Item sb, the left-sidebar panel** (a button per module and capability in Open WebUI's left sidebar; each opens a working panel), and a fix so approving a plan from the Approvals inbox starts the held task.

Install: `./items.sh sb` (or `./items.sh install`, which now includes it).

Offline results: run_checks 551 OK (80 skipped); run_addon_checks 221 OK (15 skipped); test_sidebar 11 OK; test_approvals 25 OK.

UNVERIFIED until the real server: this Open WebUI version's loader path and sidebar element, and Caddy's handling. The step refuses with a reason and changes nothing if it can't find them. Status: Built, offline-tested, awaiting live verification (L-SB).
