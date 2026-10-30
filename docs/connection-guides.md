# Connection guides

When an agent needs something the person has to go and get (an API key, a
token, a key file, an app of their own to sign in through), its entry carries a
**guide**: the steps to get it, drawn on the catalog page beside the form. It is
the first thing a new customer reads when they connect an agent, and the first
thing a demo shows, so it is held to the same standard as the product.

## When an agent needs one

`catalog import` (and so `deploy/publish-to-hosted.sh`) refuses a new version
without a guide when its connection:

- is a form with a `password` or `file` field (an API key, a token, a key file), or
- signs in through the customer's own app (`signin.client: "own-app"`).

A plain "Sign in with…" and a built-in agent ask for nothing to fetch, so they
need none. Versions published before guides existed are left as they are; the
next version of each adds one.

## What a guide holds

`connection.guide` in the entry (checked by `marketplace/entry.py`):

| Key | What it is for | Limit |
|---|---|---|
| `title` | what the person will do, e.g. "Set up a Google service account" | 80 |
| `summary` | why it works this way and what they will end up with, in two sentences | 320 |
| `time` | how long it takes someone doing it for the first time: "About 10 minutes" | 40 |
| `needs` | what to have before starting: the account, the permission, the plan | 1–4 × 140 |
| `access` | what the agent can and cannot reach with it, and where the secret is kept | 320 |
| `steps` | the steps, in order (below) | 2–10 |
| `trouble` | the errors people actually hit: `{problem, fix}` | up to 8 |
| `source` | the vendor's own documentation for this: `{label, url}` | |
| `checked` | the day someone last followed every step, `YYYY-MM-DD` | |

Each step:

| Key | What it is for |
|---|---|
| `title` | the outcome of the step, short: "Download its JSON key" |
| `text` | what to do, in a few sentences |
| `path` | the menu to click through, as the vendor names it: `["Keys", "Add key", "Create new key"]` |
| `link` | the vendor's page where the step happens: `{label, url}`, https only |
| `copy` | values to paste, each with a copy button: `["drive.googleapis.com"]` |
| `image` | a screenshot of the step: `{file, sha256, width, height, alt}` (below) |
| `tip` | the one thing people get wrong at this step |

Text is plain. Two marks are drawn: `` `code` `` for values, names and file names,
and `**bold**` for the vendor's own labels (buttons, tabs, fields). Nothing else
is: no HTML, no markdown links, no images. Links go in `link` and `source`.

## Writing one

1. **Do it first, then write it.** Follow the vendor's flow on a fresh account
   and write the steps down as you go. Docs drift from the console; the console
   is the truth.
2. **One action per step.** If a step needs "and then" twice, split it. Six or
   seven steps is typical; more than ten means the guide is doing too much.
3. **Name things exactly as the vendor does**, in bold, with the same capitals:
   **Create service account**, not "the create button".
4. **Link deep.** A step's link opens the page where the step happens, not the
   vendor's home page. Only the vendor's own https pages: never a blog or a
   video.
5. **Ask for the least.** Read-only scopes, Viewer roles, one project. Say so in
   `access`, in plain words: what it can read, what it cannot do, where the key
   is kept.
6. **End at the form.** The last step names the field, then **Test connection**
   and **Save and turn on**, as the page labels them.
7. **Trouble comes from real failures.** Quote the vendor's message as the
   `problem`, exactly as it appears, so someone searching the page finds it.
   Each `fix` says what to do and who can do it ("Ask an Organization Policy
   Administrator to…").
8. **Write for a careful person in a hurry.** Second person, present tense, no
   "simply", "just" or "easily", no adjectives about the product. Call it
   Rytangle.

## Screenshots

A step can show a screenshot of the vendor's screen, with the thing to click
boxed. They are taken during the hands-on run (below), so the pictures and
the checked steps come from the same day.

**Taking them**

- A fresh account set up the way a new customer's would be; a browser profile
  with no extensions; bookmarks bar hidden; English; zoom 100%; the window
  about 1440 × 900, the same for every shot.
- Demo names throughout: project **Rytangle demo**, service account
  **rytangle-reader**, folder **Rytangle demo files**. Never a real customer's
  name, and never the contents of a key file.
- One shot per step, taken just before the click the step names, with what it
  asks to type or choose already filled in.
- Whatever goes wrong or looks different is captured too: those become the
  guide's `trouble`.

**Preparing them**

- Crop to what the step needs: the form or dialog, not the browser around it.
- Box the control the step names (the button, field or choice) in one orange
  rounded outline; two at most.
- Leave out or blur anything that identifies an account beyond the demo names:
  numeric IDs, client IDs, avatars, other people.
- WebP, about 1280 px wide (sharp on a 2x screen), at most 512 KB; 20–70 KB is
  typical.
- `alt` says what the picture shows and what is highlighted, for anyone who
  cannot see it.

**Adding them**

Files go in `agents/<id>/guide/`, named by step (`04-json-key.webp`). Name each
in its step with `file` and `alt`, then fill in the rest:

    uv run catalog guide-images agents/<id>/catalog-<version>.json

Publishing uploads them with the entry. The catalog keeps each by its SHA-256
and serves it to the page, so a published version's pictures never change; a
new picture is a new version, like any other change.

## Checking one

Before a guide is published, someone other than its writer follows it end to
end, on an account set up the way a new customer's would be, and connects a
real server with the result. Then:

- set `checked` to that day;
- note in the pull request who followed it and on what kind of account (personal
  Google, Workspace, a new organization);
- fix anything they had to guess at, and add any error they hit to `trouble`.

Re-check a guide when the vendor changes its console, when a customer reports
being stuck, and in any case every six months. To see the dates:

    for f in agents/*/catalog-*.json; do jq -r 'select(.connection.guide) | "\(.connection.guide.checked)  \(.id) \(.version)"' "$f"; done | sort

## Shipping one

A guide is part of the entry, so changing one means a new version (a patch:
1.0.1 → 1.0.2), published like any other:

    deploy/publish-to-hosted.sh agents/<id>/catalog-<version>.json

The page shows the guide from the catalog's newest version, so it reaches every
server as soon as it is published; nobody has to update their agent to see it.

## Review checklist

Copy into the pull request:

- [ ] Every step was followed on a fresh account, and `checked` is that day
- [ ] Each step is one action, titled by its outcome
- [ ] Vendor labels are in **bold**, exactly as the vendor spells them
- [ ] Every link opens the vendor's own page where that step happens
- [ ] `access` says what the agent can reach, what it cannot, and where the secret is kept
- [ ] The narrowest access the agent works with (read-only where the vendor allows it)
- [ ] `trouble` quotes the real error messages people hit
- [ ] Every step's screenshot is from the same run, cropped, the action boxed, nothing identifying left in
- [ ] Viewed on the catalog page, in light and dark, on a phone width
