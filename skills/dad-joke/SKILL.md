# dad-joke

Fetch a random dad joke and serve it with a friendly intro. Phase 1
(Python) pulls a joke from icanhazdadjoke.com — random by default, or a
random match for a search term — and leaves a `joke.json` artifact;
phase 2 (LLM) writes a warm intro line in the user's language and
presents the joke verbatim.

Run it: `zipsa exec skills/dad-joke "dog"` (empty query → fully random
joke; a search term with no matches exits non-zero).
