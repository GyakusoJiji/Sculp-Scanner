"""デバッグポート付き Chrome の起動。

既に起動している通常の Chrome に後からデバッグポートを開くことはできず、
また実行中の Chrome と同じプロファイルは掴めないため、
専用プロファイルの別インスタンスとして起動する。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

DEFAULT_PROFILE = os.path.join(tempfile.gettempdir(), "sculp-chrome-profile")

_CANDIDATES = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
)


def find_chrome() -> str | None:
    """chrome.exe（無ければ msedge.exe）のパスを探す。"""
    # レジストリの App Paths が最も確実
    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(
                    root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
                )
                path, _ = winreg.QueryValueEx(key, "")
                if path and os.path.exists(path):
                    return path
            except OSError:
                continue
    except ImportError:
        pass

    for cand in _CANDIDATES:
        path = os.path.expandvars(cand)
        if "%" not in path and os.path.exists(path):
            return path
    return None


def launch(url: str, port: int = 9222, profile: str | None = None) -> str:
    """デバッグポート付きで Chrome を起動し、実行ファイルのパスを返す。"""
    exe = find_chrome()
    if not exe:
        raise RuntimeError(
            "chrome.exe が見つかりませんでした。Chrome のパスを直接指定して起動してください。"
        )
    profile = profile or DEFAULT_PROFILE
    os.makedirs(profile, exist_ok=True)
    subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            # 新規プロファイルだと初回セットアップ画面が出て URL が開かれないため抑止する
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-search-engine-choice-screen",
            "--disable-fre",
            url,
        ],
        close_fds=True,
    )
    return exe


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    url = argv[0] if argv else "https://www.honda.co.jp/CIVIC/configurator/"
    port = int(argv[1]) if len(argv) > 1 else 9222
    exe = launch(url, port)
    print(f"起動しました: {exe}")
    print(f"デバッグポート: {port} / プロファイル: {DEFAULT_PROFILE}")
    print("360°ビューの読み込みが終わったら Sculp-Scanner から接続してください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
