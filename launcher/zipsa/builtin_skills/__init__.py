"""Built-in skills that ship with the zipsa launcher.

Each subdirectory under this package follows the regular skill layout
(SKILL.md at the top + zipsa-dist/manifest.yaml + zipsa-dist/instruction.md).
The launcher discovers them via `paths.builtin_skill_dir(name)` so
`zipsa run <name>` and `zipsa list` see them like installed skills,
just tagged "(built-in)".

This is where authoring meta-skills live (skill-builder, future
skill-editor, skill-tester, …). User-authored skills go under
~/.zipsa/skills/ instead — they aren't allowed to use the same name
as a built-in (installer raises a clear error to avoid silent override).
"""
