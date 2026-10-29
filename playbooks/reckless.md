---
steps:
  - setup
  - {agent: build, turns: 400}
  - push-main
  - deploy
  - alert
---
Reckless mode, new project: one pass from idea to live site, straight to `main`
with no review. Riggy chose speed; still make it work well and look good.

Decide colors, names, libraries, layouts and similar yourself.

## build

1. Write a short spec paragraph in `README.md` (what it is, what it does), under
   the live URL. Put Riggy's request word for word in `docs/brief.md`.
2. Set the repo up with skills/new-repo.md. Tests are optional: the image build
   is the only required check.
3. Build the whole thing. Check it in a real browser (skills/browser.md) at phone
   size and fix what's broken or ugly.

Message: one or two sentences on what Riggy can do on the site. It's what he
reads when golem tells him the project is done.
