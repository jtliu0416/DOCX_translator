"""DOCX processing via Doctrans.DocxProc .NET CLI.

Uses a custom .NET console project built on OpenXML SDK (DocumentFormat.OpenXml 3.2.0)
to extract text units and insert bilingual translations without changing formatting.
"""

import asyncio
import json
import os
import subprocess
import sys

# __file__ = backend/app/services/docx_handler.py
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOTNET_PROJECT = os.path.join(_BACKEND_DIR, "scripts", "Doctrans.DocxProc")

SKILL_DIR = os.path.join(os.path.expanduser("~"), ".claude", "skills", "minimax-docx")
MINIMAX_CLI = os.path.join(SKILL_DIR, "scripts", "dotnet", "MiniMaxAIDocx.Cli")

DOTNET_DLL = os.path.join(DOTNET_PROJECT, "bin", "Debug", "net8.0", "Doctrans.DocxProc.dll")

# .NET executable path
DOTNET_EXE = "dotnet"
if sys.platform == "win32":
    dotnet_path = os.path.join(
        os.environ.get("ProgramFiles", "C:\\Program Files"), "dotnet", "dotnet.exe"
    )
    if os.path.exists(dotnet_path):
        DOTNET_EXE = dotnet_path


def _env():
    env = os.environ.copy()
    env["DOTNET_CLI_UI_LANGUAGE"] = "en"
    dotnet_dir = os.path.dirname(DOTNET_EXE) if DOTNET_EXE != "dotnet" else ""
    if dotnet_dir:
        env["PATH"] = dotnet_dir + os.pathsep + env.get("PATH", "")
    return env


def _ensure_built():
    if os.path.exists(DOTNET_DLL):
        return
    result = subprocess.run(
        [DOTNET_EXE, "build", DOTNET_PROJECT, "--verbosity", "quiet"],
        capture_output=True, text=True, timeout=120, env=_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f".NET build failed: {result.stderr}\n{result.stdout}")


def _run_dotnet_sync(command: str, args: list[str]) -> subprocess.CompletedProcess:
    _ensure_built()
    cmd = [DOTNET_EXE, DOTNET_DLL, command] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=_env())


async def _run_dotnet(command: str, args: list[str]) -> subprocess.CompletedProcess:
    return await asyncio.to_thread(_run_dotnet_sync, command, args)


async def extract_paragraphs(docx_path: str, output_json_path: str) -> dict:
    result = await _run_dotnet("extract", ["--input", docx_path, "--output", output_json_path])
    if result.returncode != 0:
        raise RuntimeError(f"extract_paragraphs failed: {result.stderr}\n{result.stdout}")

    with open(output_json_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def insert_translations(docx_path: str, translations_json_path: str, output_path: str,
                              paragraphs_json_path: str = ""):
    args = [
        "--input", docx_path,
        "--translations", translations_json_path,
        "--output", output_path,
    ]
    if paragraphs_json_path:
        args.extend(["--paragraphs", paragraphs_json_path])
    result = await _run_dotnet("insert", args)
    if result.returncode != 0:
        raise RuntimeError(f"insert_translations failed: {result.stderr}\n{result.stdout}")


async def validate_docx(docx_path: str) -> bool:
    """Soft validation — returns False on failure but caller should not abort."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [DOTNET_EXE, "run", "--project", MINIMAX_CLI, "--",
             "validate", "--input", docx_path, "--business"],
            capture_output=True, text=True, timeout=60, env=_env(),
        )
        return result.returncode == 0
    except Exception:
        return False
