"""Windowed entry point; keep server/model imports out of the desktop UI."""
from desktop.launcher import main


if __name__ == '__main__':
    raise SystemExit(main())
