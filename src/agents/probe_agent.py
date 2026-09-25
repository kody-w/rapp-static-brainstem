import hashlib
import json
import os
import platform
import sys
import tempfile

from agents.basic_agent import BasicAgent


def _attempt(read):
    """Return read(), or None if it fails. The probe reports what it can and never raises."""
    try:
        return read()
    except Exception:
        return None


def _in_temp_folder():
    """True when this call runs in a folder under the system temporary folder (run.py makes one per call)."""
    here = os.path.realpath(os.getcwd())
    temp = os.path.realpath(tempfile.gettempdir())
    return here != temp and here.startswith(temp + os.sep)


class ProbeAgent(BasicAgent):
    def __init__(self):
        self.name = "Probe"
        self.metadata = {
            "name": self.name,
            "description": "Prove this brainstem really ran here: returns the SHA-256 of a nonce you pass, the Python version, the operating system family, and whether it ran in a temporary folder. It reads nothing else and uses no network.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nonce": {
                        "type": "string",
                        "description": "Any text. Its SHA-256 comes back, so you can tell this run answered your call."
                    }
                },
                "required": ["nonce"]
            }
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        nonce = kwargs.get("nonce")
        text = "" if nonce is None else str(nonce)
        return json.dumps({
            "nonce_sha256": _attempt(lambda: hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()),
            "python": _attempt(platform.python_version),
            "os": sys.platform,
            "ran_in_temp_folder": _attempt(_in_temp_folder),
        })
