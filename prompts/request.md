Riggy sent a message about one of his web projects. Decide how golem should
handle it, give it a title, and return both as the structured output. Don't
use any other tools or ask questions.

- kind: `question` if he wants to know something and nothing built or changed:
  "why does the score reset?", "what model did the MVP use", "is it live yet",
  "how does the leaderboard work", "did you finish the dark mode?", "what's
  left on the spec?". Golem looks into it (the code, the site, its logs) and
  answers in a few minutes. Requests phrased as questions are not questions:
  "can you add a quiz mode?", "could you make it bigger?" are changes.
- `options` if he wants to see a few alternatives and choose between them
  before anything is built: "show me three art styles", "I'd like to decide on a
  new color scheme, give me some options", "mock up a couple of layouts and I'll
  pick", "what could the logo look like?". Golem then makes options for him to
  pick from and builds the one he picks.
- `design` if he wants the whole project to get a new look of its own, its feel
  and brand, not one piece of it: "redesign it", "give it a design pass", "I
  want it to feel like an old field guide", "it looks generic, give it some
  personality", "rebrand it with a logo and colors". Golem then makes a design
  doc with a few directions, he picks, and golem builds the look and writes it
  down for later work. One screen or one piece is not this: "redo the library
  page as a grid" is a `change`, "show me some logo ideas" is `options`.
- Otherwise `change`: he's asking for a feature, a fix or a change to be made
  ("add a quiz mode", "make it more chibi", "the score is broken"), including
  when he says to just go ahead ("just build it", "you pick"). Golem makes it
  straight away and tells him when it's live.

If he's both asking and requesting ("why is it slow? speed it up"), it's
`change`. Otherwise, if it's not clear, `question` is the cheap mistake: a
question costs a few minutes, a change can take an hour.

- title: what it's about, in 2 to 5 words of Title Case, like the name of a
  ticket. It's what golem calls the work everywhere, so it names the work
  itself, never how he put it: "I'd like you to redo the library page so it's
  a grid" is "Library View Redesign"; "the score is broken when you skip" is
  "Fix Score on Skip"; "show me three art styles" is "New Art Style"; "make it
  feel like an old field guide" is "Field Guide Redesign"; "why does the score
  reset?" is "Why the Score Resets". No quotes, no full stop.
$under_way
His message (speech-to-text, so expect typos and missing punctuation):

$request
