"""Offline standalone check, run inside the Linux candidate image with network disabled."""

import json
import subprocess
import tempfile
from pathlib import Path

from rapip.ipquality import VENDOR

with tempfile.TemporaryDirectory(prefix="ipquality-fixture-") as directory:
    root = Path(directory)
    curl = root / "curl"
    curl.write_text("""#!/usr/local/bin/python
import json,sys
args=" ".join(sys.argv[1:])
if "api.ipify.org" in args:
 print("198.51.100.10")
elif "db=ipqualityscore" in args:
 print(json.dumps({"fraud_score":73}))
elif "db=ipapi" in args:
 print(json.dumps({"company":{},"asn":{"type":"isp"}}))
else:
 print("{}")
""")
    curl.chmod(0o755)
    result = subprocess.run(
        ["bash", str(VENDOR / "runner.sh")],
        input="198.51.100.10\nsocks5h://127.0.0.1:12345\n",
        text=True,
        env={"PATH": str(root) + ":/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"},
        capture_output=True,
        timeout=30,
        check=True,
    )
    report = json.loads(result.stdout)
    assert report["Head"]["IP"] == "198.51.100.10"
    assert report["Score"]["IPQS"] == "73", "queried IPQS score must survive serialization"
    assert report["Score"]["ipapi"] == "null", "missing ipapi score must not turn into 0%"
    print("OFFLINE_RUNNER_OK: verified selected IP, IPQS serialization and missing-score preservation")
