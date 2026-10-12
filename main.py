"""Run after installing the project; never add Candidate directories to sys.path."""
from arona.main import main

if __name__ == "__main__":
    raise SystemExit(main())
