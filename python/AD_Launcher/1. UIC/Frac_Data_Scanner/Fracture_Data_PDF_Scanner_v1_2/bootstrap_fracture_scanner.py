#!/usr/bin/env python3
"""One-click environment bootstrapper for Fracture Data PDF Scanner.

This file deliberately uses only the Python standard library. It creates a
local virtual environment, verifies/install the required Python packages, checks
for Tesseract OCR, and then launches the scanner with the virtual environment's
Python interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path
from typing import Iterable, Sequence

APP_NAME = "Fracture Data PDF Scanner"
BOOTSTRAP_VERSION = "1.2"
MIN_PYTHON = (3, 10)
APP_DIR = Path(__file__).resolve().parent
APP_SCRIPT = APP_DIR / "fracture_data_pdf_scanner.py"
REQUIREMENTS_FILE = APP_DIR / "requirements_fracture_data_scanner.txt"
VENV_DIR = APP_DIR / ".venv"
LOG_FILE = APP_DIR / "setup_log.txt"
STAMP_FILE = VENV_DIR / ".fracture_scanner_requirements.sha256"
HOME_FILE = VENV_DIR / ".fracture_scanner_home.txt"
TESSERACT_PATH_FILE = APP_DIR / ".tesseract_path"

REQUIRED_IMPORTS = (
    ("fitz", "PyMuPDF"),
    ("openpyxl", "openpyxl"),
    ("pytesseract", "pytesseract"),
    ("PIL", "Pillow"),
)


def print_header() -> None:
    print("=" * 72)
    print(f"{APP_NAME} - automatic setup")
    print("=" * 72)
    print(f"Application folder: {APP_DIR}")
    print(f"Managed environment: {VENV_DIR}")
    print()


def append_log(text: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
    except OSError:
        pass


def command_text(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    printable = command_text(command)
    banner = f"\n> {printable}\n"
    print(banner, end="")
    append_log(banner)

    process = subprocess.Popen(
        [str(part) for part in command],
        cwd=str(cwd or APP_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        append_log(line.rstrip("\n"))
    return_code = process.wait()
    append_log(f"Return code: {return_code}\n")
    if check and return_code != 0:
        raise RuntimeError(f"Command failed with exit code {return_code}: {printable}")
    return return_code


def run_capture(command: Sequence[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_pythonw() -> Path:
    if os.name == "nt":
        candidate = VENV_DIR / "Scripts" / "pythonw.exe"
        if candidate.is_file():
            return candidate
    return venv_python()


def requirements_digest() -> str:
    digest = hashlib.sha256()
    digest.update(f"bootstrap={BOOTSTRAP_VERSION}\n".encode("utf-8"))
    digest.update(f"python={sys.version_info.major}.{sys.version_info.minor}\n".encode("utf-8"))
    digest.update(REQUIREMENTS_FILE.read_bytes())
    return digest.hexdigest()


def safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            shutil.rmtree(path)
            return
        except Exception as exc:  # Windows antivirus can briefly lock files.
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error


def environment_was_moved() -> bool:
    if not HOME_FILE.is_file():
        return False
    try:
        recorded = HOME_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return recorded != str(APP_DIR.resolve())


def import_check_code() -> str:
    module_names = [module for module, _package in REQUIRED_IMPORTS]
    return (
        "import importlib, sys\n"
        f"required={module_names!r}\n"
        "failed=[]\n"
        "for name in required:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        failed.append(f'{name}: {exc}')\n"
        "if failed:\n"
        "    print('\\n'.join(failed))\n"
        "    raise SystemExit(1)\n"
        "print(sys.executable)\n"
    )


def environment_is_healthy() -> tuple[bool, str]:
    python_exe = venv_python()
    if not python_exe.is_file():
        return False, "virtual-environment Python executable is missing"
    if environment_was_moved():
        return False, "application folder moved after the environment was created"
    try:
        expected = requirements_digest()
        actual = STAMP_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False, "requirements installation stamp is missing"
    if actual != expected:
        return False, "requirements changed"

    try:
        import_result = run_capture([str(python_exe), "-c", import_check_code()], timeout=60)
    except Exception as exc:
        return False, f"could not start the managed Python: {exc}"
    if import_result.returncode != 0:
        return False, f"a required import failed: {import_result.stdout.strip()}"

    try:
        pip_check = run_capture([str(python_exe), "-m", "pip", "check"], timeout=90)
    except Exception as exc:
        return False, f"pip check could not run: {exc}"
    if pip_check.returncode != 0:
        return False, f"pip reported dependency problems: {pip_check.stdout.strip()}"
    return True, "all required packages are installed"


def create_environment(rebuild: bool = False) -> None:
    if rebuild and VENV_DIR.exists():
        print("Removing the existing managed environment...")
        safe_rmtree(VENV_DIR)

    python_exe = venv_python()
    if VENV_DIR.exists() and not python_exe.is_file():
        print("The existing environment is incomplete. Rebuilding it...")
        safe_rmtree(VENV_DIR)

    if not VENV_DIR.exists():
        print(f"Creating virtual environment with Python {sys.version_info.major}.{sys.version_info.minor}...")
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(VENV_DIR)

    if not venv_python().is_file():
        raise RuntimeError(f"Virtual environment creation did not produce {venv_python()}")


def install_requirements(force_reinstall: bool = False) -> None:
    python_exe = venv_python()
    print("Preparing pip in the managed environment...")
    run_logged([str(python_exe), "-m", "ensurepip", "--upgrade"], check=False)
    upgrade_code = run_logged(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
        ],
        check=False,
    )
    if upgrade_code != 0:
        print("Warning: pip tooling could not be upgraded. Continuing with the bundled pip version.")

    command = [
        str(python_exe),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--prefer-binary",
        "--retries",
        "3",
        "--timeout",
        "60",
    ]
    if force_reinstall:
        command.extend(["--upgrade", "--force-reinstall"])
    command.extend(["-r", str(REQUIREMENTS_FILE)])
    run_logged(command)
    run_logged([str(python_exe), "-m", "pip", "check"])

    import_result = run_capture([str(python_exe), "-c", import_check_code()], timeout=90)
    if import_result.returncode != 0:
        raise RuntimeError(
            "Package installation completed, but one or more imports still failed:\n"
            + import_result.stdout.strip()
        )

    STAMP_FILE.write_text(requirements_digest(), encoding="utf-8")
    HOME_FILE.write_text(str(APP_DIR.resolve()), encoding="utf-8")


def iter_tesseract_candidates() -> Iterable[Path]:
    env_candidate = os.environ.get("TESSERACT_CMD", "").strip()
    if env_candidate:
        yield Path(env_candidate)

    try:
        saved = TESSERACT_PATH_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        yield Path(saved)

    found = shutil.which("tesseract")
    if found:
        yield Path(found)

    if os.name != "nt":
        return

    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("ChocolateyInstall", r"C:\ProgramData\chocolatey"),
    ]
    fixed = [
        Path(roots[0]) / "Tesseract-OCR" / "tesseract.exe",
        Path(roots[1]) / "Tesseract-OCR" / "tesseract.exe",
    ]
    if roots[2]:
        fixed.extend(
            [
                Path(roots[2]) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
                Path(roots[2]) / "Tesseract-OCR" / "tesseract.exe",
            ]
        )
    if roots[3]:
        fixed.append(Path(roots[3]) / "bin" / "tesseract.exe")
    yield from fixed

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_packages.is_dir():
            try:
                yield from winget_packages.glob("*Tesseract*/*/tesseract.exe")
                yield from winget_packages.glob("*Tesseract*/tesseract.exe")
            except OSError:
                pass


def find_tesseract() -> Path | None:
    seen: set[str] = set()
    for candidate in iter_tesseract_candidates():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            resolved = candidate.expanduser()
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file():
            return resolved
    return None


def install_tesseract_with_winget() -> Path | None:
    if os.name != "nt":
        return None
    winget = shutil.which("winget")
    if not winget:
        print("Windows Package Manager (winget) was not found.")
        return None

    package_ids = ("tesseract-ocr.tesseract", "UB-Mannheim.TesseractOCR")
    for package_id in package_ids:
        print(f"Trying to install Tesseract OCR package: {package_id}")
        return_code = run_logged(
            [
                winget,
                "install",
                "--id",
                package_id,
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--silent",
            ],
            check=False,
        )
        if return_code == 0:
            time.sleep(2)
            found = find_tesseract()
            if found:
                return found
            print("The installer reported success, but tesseract.exe was not located yet.")
    return None


def tesseract_setup(prompt: bool, force_install: bool) -> Path | None:
    found = find_tesseract()
    if found:
        print(f"Tesseract OCR found: {found}")
        try:
            TESSERACT_PATH_FILE.write_text(str(found), encoding="utf-8")
        except OSError:
            pass
        return found

    print()
    print("Tesseract OCR was not found.")
    print("It is needed only for image-only/scanned PDFs. Searchable PDFs can still be scanned.")

    should_install = force_install
    if prompt and not force_install and os.name == "nt" and sys.stdin.isatty():
        try:
            answer = input("Install Tesseract OCR now using winget? [Y/n]: ").strip().lower()
        except EOFError:
            answer = "n"
        should_install = answer in ("", "y", "yes")

    if should_install:
        found = install_tesseract_with_winget()
        if found:
            print(f"Tesseract OCR installed: {found}")
            try:
                TESSERACT_PATH_FILE.write_text(str(found), encoding="utf-8")
            except OSError:
                pass
            return found
        print("Automatic Tesseract installation was not successful.")

    print("You may install Tesseract later and select tesseract.exe in the scanner GUI.")
    return None


def launch_application(app_args: Sequence[str], tesseract_path: Path | None) -> int:
    env = os.environ.copy()
    env["FRACTURE_SCANNER_BOOTSTRAPPED"] = "1"
    env["FRACTURE_SCANNER_VENV"] = str(VENV_DIR)
    if tesseract_path:
        env["TESSERACT_CMD"] = str(tesseract_path)

    is_cli = "--folder" in app_args
    python_exe = venv_python() if is_cli else venv_pythonw()
    command = [str(python_exe), str(APP_SCRIPT), *app_args]

    if is_cli:
        print("Launching command-line scan in the managed environment...")
        return subprocess.call(command, cwd=str(APP_DIR), env=env)

    print("Launching the scanner GUI in the managed environment...")
    subprocess.Popen(command, cwd=str(APP_DIR), env=env)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and launch the Fracture Data PDF Scanner.")
    parser.add_argument("--setup-only", action="store_true", help="Prepare the environment but do not launch the app.")
    parser.add_argument("--rebuild", action="store_true", help="Delete and recreate the managed virtual environment.")
    parser.add_argument(
        "--force-reinstall",
        action="store_true",
        help="Reinstall all required Python packages even if the environment appears healthy.",
    )
    parser.add_argument(
        "--install-tesseract",
        action="store_true",
        help="Attempt to install Tesseract with winget when it is missing.",
    )
    parser.add_argument(
        "--no-tesseract-prompt",
        action="store_true",
        help="Do not ask whether Tesseract should be installed.",
    )
    parser.add_argument("app_args", nargs=argparse.REMAINDER, help="Arguments passed to the scanner after --.")
    args = parser.parse_args()
    if args.app_args and args.app_args[0] == "--":
        args.app_args = args.app_args[1:]
    return args


def main() -> int:
    args = parse_args()
    print_header()
    append_log("\n" + "=" * 72)
    append_log(time.strftime("Setup started: %Y-%m-%d %H:%M:%S"))
    append_log(f"Base Python: {sys.executable} ({sys.version})")
    append_log(f"Application folder: {APP_DIR}")

    if sys.version_info < MIN_PYTHON:
        print(
            f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required. "
            f"The detected version is {sys.version_info.major}.{sys.version_info.minor}."
        )
        return 2
    try:
        import tkinter  # noqa: F401 - availability check for the GUI
    except Exception as exc:
        print("ERROR: This Python installation does not include Tkinter/Tcl-Tk support.")
        print("Reinstall Python with the Tcl/Tk and IDLE component enabled.")
        print(f"Details: {exc}")
        return 2
    if not APP_SCRIPT.is_file():
        print(f"ERROR: Application file is missing: {APP_SCRIPT}")
        return 2
    if not REQUIREMENTS_FILE.is_file():
        print(f"ERROR: Requirements file is missing: {REQUIREMENTS_FILE}")
        return 2

    try:
        create_environment(rebuild=args.rebuild)
        healthy, reason = environment_is_healthy()
        if args.force_reinstall:
            healthy = False
            reason = "forced package reinstall requested"
        if healthy:
            print(f"Environment check passed: {reason}.")
        else:
            print(f"Environment setup is required because {reason}.")
            install_requirements(force_reinstall=args.force_reinstall)
            healthy, reason = environment_is_healthy()
            if not healthy:
                raise RuntimeError(f"Environment verification failed after installation: {reason}")
            print("Environment verification passed.")
    except Exception as exc:
        append_log(f"SETUP ERROR: {exc}")
        print()
        print("SETUP FAILED")
        print(str(exc))
        print()
        print(f"Detailed setup output was written to: {LOG_FILE}")
        print("If this is a company-managed computer, a proxy or software policy may be blocking pip.")
        return 1

    is_cli = "--folder" in args.app_args
    prompt_tesseract = not args.no_tesseract_prompt and not is_cli
    tesseract_path = tesseract_setup(prompt=prompt_tesseract, force_install=args.install_tesseract)

    if args.setup_only:
        print()
        print("Setup completed successfully. You may now run Start_Fracture_Data_Scanner.bat.")
        return 0

    return launch_application(args.app_args, tesseract_path)


if __name__ == "__main__":
    raise SystemExit(main())
