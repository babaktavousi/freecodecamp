#!/usr/bin/env python3
"""Batching Plant Simulator for Microsoft Visio.

  python main.py                      GUI (reads the drawing open in Visio, or open a .vsdx/.json)
  python main.py cli plant.vsdx ...   headless pipeline (see --help)
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from bpsim.cli import main
        sys.exit(main(sys.argv[2:]))
    from bpsim.ui import run
    run(sys.argv[1:])
