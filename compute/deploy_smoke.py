"""Deploy smoke test: import the app with ONLY deploy packages.

The deployed environment installs requirements.txt (streamlit, pandas,
numpy, plotly) — far thinner than the dev venv, which has masked two
deploy breaks already (a shapely import, a stale-module ImportError).
This script keeps a second venv (venv-deploy/, gitignored) holding
exactly the deploy packages and imports app/streamlit_app.py inside
it. Import errors fail the test and send an alert email; any other
exception is tolerated (bare-mode Streamlit raises benign noise).

Run manually before pushing app changes, and by run_update.sh on
dekad days: python compute/deploy_smoke.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENV = ROOT / "venv-deploy"
PY = VENV / "bin" / "python"

PROBE = r"""
import sys
sys.path.insert(0, "app")
try:
    import streamlit_app  # noqa: F401
except (ImportError, ModuleNotFoundError) as e:
    print(f"DEPLOY-SMOKE-FAIL: {type(e).__name__}: {e}")
    sys.exit(1)
except BaseException as e:  # bare-mode Streamlit noise is fine
    print(f"deploy smoke: tolerated {type(e).__name__}: {e}")
print("deploy smoke OK")
"""


def main() -> int:
    if not PY.exists():
        print("creating venv-deploy (first run)...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)],
                       check=True)
        subprocess.run([str(VENV / "bin" / "pip"), "install", "-q",
                        "-r", str(ROOT / "requirements.txt")], check=True)
    r = subprocess.run([str(PY), "-c", PROBE], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    out = (r.stdout + r.stderr).strip()
    print(out[-2000:])
    if r.returncode != 0:
        try:
            sys.path.insert(0, str(ROOT / "compute"))
            from flood_signals import _email
            _email("CHIRPS monitor — deploy smoke test FAILED",
                   "app/streamlit_app.py does not import with only the "
                   "deploy packages (requirements.txt). The deployed "
                   "dashboard will crash on next redeploy.\n\n" + out)
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
