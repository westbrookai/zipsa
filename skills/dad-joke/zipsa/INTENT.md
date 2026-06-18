# Intent — dad-joke

Why: a quick, friendly laugh on demand — a dad joke delivered with a
warm intro, without the user hitting an API or reading raw JSON.

What the user wanted: optionally give a search term (empty → fully
random), get a dad joke presented verbatim with a short friendly intro
in their own language.

Boundary: dad jokes only (icanhazdadjoke.com); the joke text is never
translated or reworded (English wordplay loses the pun). A search term
with no matches exits non-zero rather than inventing a joke.
