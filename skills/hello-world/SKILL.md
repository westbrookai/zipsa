---
name: hello-world
description: Smoke test for the zipsa runtime — reports the container environment, then has an LLM greet the user and name its model. Use to verify the platform's deterministic and LLM halves are both alive.
---

# hello-world

Smoke test for the zipsa runtime. Phase 1 (Python) reports the
container environment; phase 2 (LLM) greets the user in their language
and names its model — proving both the deterministic and LLM halves of
the platform are alive.

Run it: `zipsa exec skills/hello-world "안녕!"`
