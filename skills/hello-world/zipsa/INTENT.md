# Intent — hello-world

Why: a smoke test proving the zipsa runtime's two halves — deterministic
code phases and LLM phases — are both alive, with no external
dependencies to get in the way.

What the user wanted: run the skill and get back the container
environment (code phase) plus a friendly greeting that names the model
(LLM phase), in the user's language.

Boundary: diagnostics only — no real work, no network, no credentials.
