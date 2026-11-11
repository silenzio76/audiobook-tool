"""Compatibility entry point for the pinned isolated Qwen installer."""
import sys
from install_environment import main


if __name__ == '__main__':
    main(['--profile', 'qwen', *sys.argv[1:]])
