"""macOS notification helper shared by the daily token-health checks --
launchd runs these unattended and nobody was reading the log output, so a
warn/fail needs to actually surface instead of sitting in a file."""
import subprocess


def notify_macos(title: str, message: str, sound: str = "Basso") -> None:
    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        f'display notification "{_escape(message)}" '
        f'with title "{_escape(title)}" sound name "{sound}"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except OSError:
        # No osascript (not macOS, or no GUI session) -- the check's own
        # print()/exit code still carries the result, so don't let a
        # missing notifier take down the health check itself.
        pass
