#!/usr/bin/env python3
# ASCII-named launcher for 感知哨兵.py (cmd.exe .bat files must stay ASCII).
import runpy, sys, pathlib
sys.argv[0] = str(pathlib.Path(__file__).with_name("感知哨兵.py"))
runpy.run_path(sys.argv[0], run_name="__main__")
