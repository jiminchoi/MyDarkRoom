"""PyInstaller Windows entry point; keep native imports behind the launcher UI."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from darkroom.desktop import main
    raise SystemExit(main())
