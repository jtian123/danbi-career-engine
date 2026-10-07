# Setting up Danbi's career engine on her Mac (instructions for Claude)

Hi Claude — you're helping **Danbi Jang** set up her internship search engine and Career Hub on her
own Mac. James built it; this note is everything you need. Work through the steps in order, run
the commands yourself, explain each step to her in one plain sentence, and stop to ask her only
where a step says so. Afterwards, read `AGENTS.md` fully — it is the rulebook for everything you do
in this repo.

What she gets:
- **Career Hub** — a dashboard at http://127.0.0.1:7768 that is always running (it starts at login).
  A calendar where each day holds that day's internships, application tracking, an Explore view of
  directions and industries, and a USC tab (fairs, events, advising, alumni).
- **Daily discovery** — when she asks "find me internships today", you scan ~1,400 public career
  sources (big employers first), review the best ~40 postings, and put them on today's list.
- **Résumés** — one-page, source-backed résumés for a specific posting.

---

## Step 1 — Python
```sh
python3 --version
```
Needs 3.8 or newer. If macOS asks to install the Command Line Developer Tools, let it (Install →
Agree), then run the command again. Nothing else is needed for the hub and the job search.

## Step 2 — Get the code
Put it in her home folder — **not** in Documents, Desktop or Downloads (macOS stops background
services from reading those folders, and the hub runs in the background).
```sh
git clone https://github.com/jtian123/danbi-career-engine.git ~/danbi-career-engine
cd ~/danbi-career-engine
```
(If `git` isn't available yet, the Command Line Tools from Step 1 provide it.)

## Step 3 — Her private data (ask her for the zip)
The GitHub code has **no personal data**. Her résumé facts, the hub database (with the internships
already reviewed for her on Oct 6, 2026) and the scanner's memory come in a separate file James
sends her: **`danbi-private-data.zip`** (by AirDrop or Messages). Ask her where it is — usually
`~/Downloads`. Then unzip it into the engine folder:
```sh
ditto -x -k ~/Downloads/danbi-private-data.zip ~/danbi-career-engine
ls ~/danbi-career-engine/data        # expect: profile.json master_profile.json resume/ hub/ state/ …
```
If the terminal can't read Downloads ("Operation not permitted"), ask her to click **Allow** on the
macOS prompt, or to drag the zip from Finder into `~/danbi-career-engine` and unzip it there
(`ditto -x -k danbi-private-data.zip .`).

`data/` is private: never commit it, upload it, or paste it anywhere public. It is git-ignored.

## Step 4 — Check the machine
```sh
python3 career.py doctor
```
It lists what's present and what's missing, in plain words. Required items must be ✓ before you go
on. The résumé items can wait until Step 6.

## Step 5 — Start the Career Hub (stays on)
```sh
python3 career.py hub install
```
This installs a small background service (`com.danbi.careerhub`) that starts at every login and
restarts itself if it ever stops, then opens http://127.0.0.1:7768. Show her:
- **Today** — click a day in the calendar; the right side is that day's list (internships and
  full-time university staff roles, with a switch to show either) ("Apply first",
  "Check before applying", her own finds, campus events). Oct 6, 2026 already has a reviewed list.
- On each job: **Interested / Curious / Not for me** (and why) — this is how the hub learns what she
  likes; **status** (Saved → Applied → Interviewing …); notes; "USC alumni at …"; and "Copy résumé
  request for Claude".
- **Applications** — her board, plus "Add a job you found yourself" for Handshake, fairs, referrals.
- **Explore** — which directions and industries her ratings favor, and every job found so far.
- **USC** — campus resources and dated events.

Optional: drag `Career Hub.command` (in the engine folder) to the Dock — one click reopens the hub.
Bookmark http://127.0.0.1:7768 too.

## Step 6 — Résumé tools (needed before the first résumé)
```sh
python3 -m pip install --user -r requirements.txt
```
Then LibreOffice (turns the Word file into a PDF without Word's file-access popups). With Homebrew:
`brew install --cask libreoffice`. Without Homebrew, ask her to download it from
https://www.libreoffice.org/download/ and drag it to Applications. Run `python3 career.py doctor`
again — everything should be ✓.

## Step 7 — Make sure it all works
```sh
python3 -m unittest discover -s tests      # should end with "OK"
python3 career.py hub status               # agent loaded, port answering
```

## Step 8 — Remember her
Read `data/ABOUT_ME.md` (private, from the zip) and save its key points to your memory — who she is,
what she wants from the search, and the open facts to confirm with her. Also remember:
- The engine lives at `~/danbi-career-engine`; the hub is http://127.0.0.1:7768; read AGENTS.md
  before any scan, résumé or code change.
- Never apply, email, or log in on her behalf. Never move `data/` anywhere public.
- When she states a lasting preference, write it into `data/ABOUT_ME.md` too.

---

## How she'll use it (tell her these phrases)
- **"Find me internships today."** → AGENTS.md §3: `python3 career.py scan`, review the queue with
  `prompts/job_review.md` (split ~40 jobs across 4 parallel reviewers), `python3 career.py mark …`.
  Then a short chat summary: how many to apply to first, the top few by name, deadlines this week,
  campus events this week. The full list is in the hub.
- **"Build my résumé for <job>."** (or paste the hub's "Copy résumé request") → AGENTS.md §6 and
  `docs/RESUME_WORKFLOW.md`.
- **"What do my ratings say? What should I explore next?"** → `python3 career.py prefs` and the
  Explore tab; suggest one or two directions or industries she hasn't rated yet.
- **"What's happening at USC this month?"** → the USC tab; refresh `registry/campus.json` from the
  Viterbi Career Connections and USC Career Center pages if it's stale, then
  `python3 career.py campus-sync`.
- **"Any good university jobs?"** → they come in with every daily scan (full-time staff roles,
  tagged in the day list — use its "Full-time staff" switch); summarize the reviewed ones.
- **"I found this on Handshake: …"** → `python3 career.py lead "Company" "Title" --url … --source handshake`.

## Updates from James
```sh
cd ~/danbi-career-engine && git pull
launchctl kickstart -k gui/$(id -u)/com.danbi.careerhub    # restart the hub on the new code
python3 -m unittest discover -s tests
```
Her data is never touched by `git pull`.

## If something goes wrong
- Hub page won't open: `python3 career.py hub status`, then `tail -50 ~/Library/Logs/danbi-career-hub.log`.
  Reinstall with `python3 career.py hub install`.
- "Operation not permitted" in the log: the folder is under Documents/Desktop/Downloads — move it to
  `~/danbi-career-engine` and run `hub install` again.
- A scan prints a WARNING about a source: say so in your summary; it is not fatal. LinkedIn blocks
  are expected now and then — never retry or work around them.
- Port 7768 busy: `lsof -i :7768` shows who has it; the hub only ever runs once, under launchd.
- To remove the service: `python3 career.py hub uninstall` (her data stays).
