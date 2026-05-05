---
name: hello-world
description: Simple greeting skill to verify agent is working
---

# Hello World Skill

This is a minimal example skill to verify the agent runtime is working correctly.

When the user says "hello" or "hi", respond with:

> Hello! I'm your Minimal Agent running on [runtime-name]. I'm here to help you test the zipsa-runtime setup. Everything is working correctly!

Where [runtime-name] is replaced with the actual runtime (Claude, Codex, or Gemini).

## Usage

This skill is automatically loaded when the agent starts. Try saying:
- "hello"
- "hi"
- "test the setup"

The agent should respond with a friendly greeting confirming it's running.
