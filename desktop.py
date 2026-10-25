"""PyInstaller Windows entry point; keep native imports behind the launcher UI."""
import multiprocessing
import sys

# Imported configuration modules must never create caches inside the EXE folder.
sys.dont_write_bytecode = True

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from darkroom.desktop import main
    raise SystemExit(main())
