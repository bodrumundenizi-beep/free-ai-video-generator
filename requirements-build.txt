# Build-only dependencies.
# PyInstaller is pinned because bootloader changes between minor versions are a
# common cause of "builds fine locally, flagged by antivirus in CI".
pyinstaller==6.22.2
pyinstaller-hooks-contrib>=2026.7
pytest==9.1.1           # run before every release build
# Pillow (for packaging/make_icon.py) is pinned in requirements.txt.
