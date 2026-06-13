"""Enable `python -m zipsa` — a PATH-independent CLI entrypoint.

Used by `zipsa create` to give the spawned authoring agent a zipsa
command that works regardless of whether `zipsa` is on its PATH.
"""

from zipsa.cli import app

if __name__ == "__main__":
    app()
