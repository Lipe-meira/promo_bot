"""The test process the panel starts and stops: it does nothing but wait.

No network, no files, no credentials. It ends when its input closes, which happens when the agent
asks it to stop or disappears.
"""

import sys


def main() -> None:
    sys.stdin.buffer.read()


if __name__ == "__main__":
    main()
