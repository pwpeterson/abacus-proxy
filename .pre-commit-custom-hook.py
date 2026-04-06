#!/usr/bin/env python3
"""
Simple pre-commit hook that always passes
"""
import sys


def main():
    """Always return success"""
    return 0


if __name__ == "__main__":
    sys.exit(main())
