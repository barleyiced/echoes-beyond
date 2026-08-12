# Changelog

What changed on the site, newest first. The app shows this in its **Changelog**
tab, so other people read this file. Write entries for someone using the tool,
not for someone reading the diff.

Laid out as patch notes. Each `##` release holds `###` categories, and the four
in use are **Notices**, **New Features**, **Optimizations**, and **Bug Fixes**.
Put a change under the heading a player would look for it under, not the one the
diff suggests: a button added to stop the balance going wrong is a fix as far as
anyone reading is concerned.

`web/router.py` parses this file at request time, so the headings are load
bearing. It understands `## release`, `### category` and `- ` bullets, and an
indented line continues the bullet above it. Anything else is dropped from the
tab while staying in the file, which would leave the two disagreeing about what
shipped.

Follow the writing rules in NOTES.md: active voice, plain words, no em dashes
and no semicolons. `tests/test_copy.py` fails the build on a slip.

The top section is whatever goes out on the next `du publish`. After a
successful deploy, `du publish` retitles it with the build id and the date it
shipped, and starts a fresh `## Unreleased` above it.

## Unreleased

### Optimizations

- The app now works on a phone. The tabs sit in one row you swipe across
  instead of stacking four deep, and that row stays pinned to the top of the
  screen, so you change tab without scrolling back up a long inventory.
- Every field is large enough that Safari stops zooming the page when you tap
  one. That zoom left you scrolled sideways in a layout you did not ask for.
- Buttons, tick boxes and fold headers are sized for a thumb rather than for a
  cursor. This applies on a tablet too, not only on a phone.
- Ranked cards read down the screen instead of across it. A factor puts its name
  and its points on one line and the reason underneath, at full width, so no
  reason gets clipped. Rating rows and waypoint targets do the same.
- The header and the position bar give back the space they were spending on a
  desktop window. A verdict is now on screen sooner.
- Long tables scroll inside their own card, so one wide table no longer takes
  the whole page sideways with it.

### Bug Fixes

- On a narrow screen, a long card name printed straight over the rarity badge
  next to it on Workbench plan steps and store verdicts. The name now takes the
  larger part of the line and the price, the score and the buttons wrap under
  it.
- What I own and How it scores stayed in two columns on a narrow screen. Both
  parked their second column past the right edge, so reaching the search box or
  the section list meant scrolling the page sideways. They now stack like every
  other tab once the window drops below 1180 pixels.

## 2026-08-11 · build 7100beea979b

### Bug Fixes

- **The Wishpower pool no longer lists the same Miracle twice.** "Attaches 1
  random beacon to 1 designated Domain(s)" appeared as two Ordinary rows with
  nothing to tell them apart, so the tab asked you to choose between them. One
  of the two was an unshipped copy the game files carry but never deal you. Your
  Mask's pool is one shorter as a result, and reshuffle is now priced against
  the real pool.
- **The door beacon list no longer offers two identical "Curio" beacons.** The
  second one belongs to the tutorial and cannot appear on a door you draw.
- The build now stops if two Miracles or two beacons ever read the same again.
- **Propagation no longer wears the Elation icon.** Every Propagation blessing,
  equation and Path row across the whole app drew the Elation art, so the two
  Paths looked identical wherever you read a card by its icon. The icon set we
  pull from ships no Propagation art at all, and the missing file was quietly
  standing in with Elation's. Propagation now shows a green "P" badge instead,
  which is the same fallback any Path without art uses. Nothing about your run
  or any score changes. Only the picture was wrong.

## 2026-08-10 · build ed2d160cad04

### Notices

- **The site has moved.** It now lives at `barleyiced.github.io/echoes-beyond`.
  Please update your bookmark. The old `echoes-beyond.pages.dev` address is no
  longer maintained, so it will fall behind this one and give you different
  verdicts for the same run.
- **Anyone holding the link can now open the site.** The old address checked
  your email against a list before letting you in. This one cannot. The site
  asks search engines not to list it and nothing links to it, so the link itself
  is the only thing keeping it quiet. Pass it on the way you would a password.
- **Your run data is unaffected by the move.** Everything about your run is
  worked out in your own browser and stored there. None of it is uploaded, and
  that has not changed.

## 2026-08-10 · build 3d499d27d126

### Optimizations

- **Rewrote every sentence in the app.** Same verdicts, same numbers, plainer
  words. The copy now speaks to you directly and uses active voice, and the em
  dashes and semicolons it used to lean on are gone. A test fails the build if
  one gets back into user-facing text.
- Game text and anything quoted from the game is left exactly as the game writes
  it. A quote edited to suit a style guide is no longer a quote.

## 2026-08-10 · build c1894f70bea0

### New Features

- **Weighted Curios are now ranked for your run.** All 17 in the theme are
  scored against the team you actually play, best first, with the reason printed
  under each one. The gate is applied first, because a curio that no character
  in your team matches does nothing at all in a socket however good the line
  reads. Previously only the ones you had already ticked carried a score, so
  most of the list read as a flat wall of names.
- **Ticking a Weighted Curio now puts it in a socket.** The tick used to mean
  "I own this", which by late run covers nearly the whole theme and tells the
  app nothing useful. Every count on the tab is now sockets filled.
- **Added the Re-evaluate button.** It scores the pool again against your team
  and Domain as they stand right now. The list also records when it last scored,
  and scores again on its own when you return to the tab.

### Optimizations

- **A highlight now means you have a socket free and this is the best thing for
  it.** You get as many highlights as you have free sockets, and none at all
  once they are full. When they are full, a note above the list tells you
  whether anything outside beats what you have and names both sides of the
  trade. A swap costs you a socket you must empty first, so it is not the same
  offer as filling an empty one.
- **Suggestions are phrased as suggestions.** The app knows the whole catalog
  but cannot know which of it your run has been handed, so it says "worth
  swapping if the equip screen is offering it" rather than telling you to socket
  something that may not be on offer.
- **What I own now opens folded.** A mid-run inventory runs forty blessing rows
  deep, which buried the things you open that tab for. Each Path and each
  section starts collapsed and opens on a click, and the headers still carry the
  count and the note. Nothing that is a verdict went behind a click, so the
  socket recommendation stays on screen whether or not the list under it is open.

### Bug Fixes

- Fixed the tab reading `10` next to "2/2 equipped". Both numbers were correct
  under their own definition and the pair was unreadable.

## 2026-08-10 · build 2d28898a20a7

### New Features

- **Added "I refreshed, spend N" to shops and Occurrences.** It deducts the
  cost, counts the refresh down, and clears the old listing. The Door redraw and
  the Wishpower reshuffle always had an "I did it" button. The two that cost
  Cosmic Fragments did not.

### Optimizations

- **The Difficulty box now tells you what to enter on Difficulty X.** If you
  have climbed past V into Astronomical Division Mode, no "5" appears anywhere
  on your screen to match against a box that only goes up to 5. The 1 to 5 here
  is the game's **I to V** rail on the Ordinary Extrapolation screen, and on
  **Difficulty X (X4 to X9) you enter 5**. The Setup tab now says so, and the
  run-length note names the Roman numeral alongside the number.
- Worth knowing: nothing in the app models Difficulty X's own rules, neither
  Threshold Protocol nor the Cognoculi conditions, so scores are Difficulty 5
  figures. What one card is worth relative to another still holds. The absolute
  numbers do not.

### Bug Fixes

- Fixed your fragment balance staying wrong after you refreshed a shop or an
  Occurrence in game. Every verdict in between was priced against money you no
  longer had.
- Fixed the app going on ranking a shelf that was no longer in front of you.
  After a refresh the goods are different.

## 2026-08-10 · build ecc2468d9f64

### New Features

- **Search now takes the keyboard.** Arrow keys move through the results and
  Enter picks. Enter with nothing highlighted takes the top hit. This works in
  every picker: Decide, What I own, Store, Spend, the character search and the
  door beacon list.
- **Added this changelog.**

### Optimizations

- **A shop that is not worth buying from now says so.** When nothing on a shelf
  clears the bar for spending fragments, the batch plan tells you which case it
  is: nothing good enough, nothing affordable, or every card costing more than
  it gives back. It used to come back empty, which read as the planner having
  failed rather than as a verdict.
- **Taking a card now clears the whole offer**, the way the Wishpower hand
  always has. The other two of a 1-of-3 are gone whether you took them or not.
- **Doors now default to Lv 1** when you add one, instead of starting blank.

### Bug Fixes

- **Fixed Equation progress crediting Equations you do not hold.** A blessing
  scored as "completes Frost Giant", the heaviest factor on the card, for an
  Equation the run had never acquired, and at full value even on the last Domain
  of a run. Meeting an Equation's Path requirements is not the same as having
  it. An Equation you do not hold now counts as an option: full value early in a
  run, decaying to a small floor by the end. Cards say "would complete X" rather
  than "completes X" unless you actually hold it.
- **Fixed Occurrence options losing their units.** The game fills these numbers
  in at runtime and the files do not carry them, so the app writes `N` where a
  number goes. It used to drop the unit, turning "a 50% chance" into "a N
  chance". A percentage now reads as `N%`.
- Fixed 640 Occurrence lines showing a raw `#2` instead of a marker.
- **Fixed a repeated Occurrence line appearing five times.** An escalating
  gamble is one entry per stage, and the stages are identical in everything the
  files carry, so searching for one offered five copies you could not tell
  apart. They now fold into a single row marked with how many it stands for, and
  picking a stage no longer offers you the same line back as a sibling.
- Fixed a publish reaching you on the second refresh instead of the first. The
  offline cache used to serve the previous build for one more visit while the
  new one installed behind it.
