Riggy sent a message about one of his web projects. Decide how golem should
handle it and return it as the structured output. Don't use any other tools or
ask questions.

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
- Otherwise `change`: he's asking for a feature, a fix or a change to be made
  ("add a quiz mode", "make it more chibi", "the score is broken"), including
  when he says to just go ahead ("just build it", "you pick"). Golem makes it
  straight away and tells him when it's live.

If he's both asking and requesting ("why is it slow? speed it up"), it's
`change`. Otherwise, if it's not clear, `question` is the cheap mistake: a
question costs a few minutes, a change can take an hour.

His message (speech-to-text, so expect typos and missing punctuation):

$request
