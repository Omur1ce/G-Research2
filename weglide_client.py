
"""
weglide_client.py
------------------
Zero-dependency (requests-only) client for WeGlide's public API.
Focus: thermal replay + useful helpers.

Usage (library):
    from weglide_client import WeGlideClient
    wg = WeGlideClient()  # or WeGlideClient(token="...") if you later need OAuth
    thermals = wg.get_thermals(time_unix=1748779200)
    print(thermals[:2])

Usage (CLI):
    python weglide_client.py thermal --time 1748779200
    python weglide_client.py flight --id 123456
"""
import os
import sys
import json
import time as _time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

try:
    import requests
except Exception as e:
    raise SystemExit("This client requires the 'requests' package. Install with: pip install requests") from e


API_BASE = "https://api.weglide.org/v1"


@dataclass
class WeGlideClient:
    token: Optional[str] = None
    timeout: Union[int, float] = 30
    _session: Optional[requests.Session] = None

    def __post_init__(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "weglide-client/1.0"})
            if self.token:
                self._session.headers.update({"Authorization": f"Bearer {self.token}"})
        # Allow token from env if not passed
        if not self.token:
            env_token = os.getenv("WEGLIDE_TOKEN")
            if env_token:
                self.token = env_token
                self._session.headers.update({"Authorization": f"Bearer {self.token}"})

    # -------------------------- Public methods --------------------------

    def get_thermals(self, time_unix: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch thermal 'replay' at a given Unix timestamp (seconds).
        Public read does not require auth.
        """
        if time_unix is None:
            time_unix = int(_time.time())
        url = f"{API_BASE}/thermal"
        params = {"time": int(time_unix)}
        r = self._session.get(url, params=params, timeout=self.timeout)
        self._raise_for_status(r)
        data = r.json()
        if not isinstance(data, list):
            # Some endpoints return dict; thermal should be list of items.
            # Keep it robust by wrapping into list if needed.
            data = [data]
        return data

    def get_flight_detail(self, flight_id: Union[str, int]) -> Dict[str, Any]:
        """
        Fetch per-flight analysis (includes thermal aggregates per leg).
        """
        url = f"{API_BASE}/flightdetail/{flight_id}"
        r = self._session.get(url, timeout=self.timeout)
        self._raise_for_status(r)
        return r.json()

    def get_fixes_batch(self, time_unix: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetch a synchronized batch of GNSS fixes (pairs well with thermals for replay UIs).
        """
        if time_unix is None:
            time_unix = int(_time.time())
        url = f"{API_BASE}/fix/batch"
        params = {"time": int(time_unix)}
        r = self._session.get(url, params=params, timeout=self.timeout)
        self._raise_for_status(r)
        return r.json()

    # -------------------------- Helpers --------------------------

    def _raise_for_status(self, r: requests.Response) -> None:
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            # Print server payload for easier debugging
            msg = f"HTTP {r.status_code} calling {r.url}"
            try:
                payload = r.json()
                msg += f"\nResponse JSON: {json.dumps(payload, ensure_ascii=False)[:1000]}"
            except Exception:
                msg += f"\nResponse text: {r.text[:1000]}"
            raise requests.HTTPError(msg) from e


# ------------------------------ CLI ----------------------------------
def _cli(argv: List[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="WeGlide public API client (thermals + helpers)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("thermal", help="Fetch thermals for a given Unix time (seconds)")
    p1.add_argument("--time", type=int, help="Unix timestamp in seconds (default: now)")

    p2 = sub.add_parser("flight", help="Fetch per-flight analysis")
    p2.add_argument("--id", required=True, help="WeGlide flight id (numeric)")

    p3 = sub.add_parser("fixes", help="Fetch batch of GNSS fixes for a given Unix time (seconds)")
    p3.add_argument("--time", type=int, help="Unix timestamp in seconds (default: now)")

    parser.add_argument("--token", help="Optional OAuth token (WEGLIDE_TOKEN env also accepted)")
    parser.add_argument("--timeout", type=float, default=30, help="HTTP timeout in seconds (default: 30)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")

    args = parser.parse_args(argv)

    client = WeGlideClient(token=args.token, timeout=args.timeout)

    if args.cmd == "thermal":
        data = client.get_thermals(time_unix=args.time)
    elif args.cmd == "flight":
        data = client.get_flight_detail(args.id)
    elif args.cmd == "fixes":
        data = client.get_fixes_batch(time_unix=args.time)
    else:
        parser.error("Unknown command")

    if args.pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(data, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
