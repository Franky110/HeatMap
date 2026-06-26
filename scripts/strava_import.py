"""Strava import GUI: fetch new activities from a Strava account (via OAuth2)
and download them as GPX files into the trip library (SOURCE_DIR).

Strava's API requires every user to register their own (free) "API
application" at https://www.strava.com/settings/api to get a Client ID and
Client Secret -- there's no shared default app. The Client ID/Secret and the
resulting refresh token are stored (token encrypted) in the data folder.

Strava doesn't expose a direct "download as GPX" endpoint; instead we fetch
the activity's lat/lng + altitude + time streams and build a GPX file from
them.
"""

import os
import json
import base64
from datetime import datetime

import requests

from trip_manager import DATA_DIR
from dpapi_utils import dpapi_encrypt, dpapi_decrypt, secure_credential_file
from activity_import_base import ImportWindowBase, run_oauth_flow, build_gpx

import tkinter as tk
from tkinter import ttk, messagebox

CREDENTIALS_PATH = os.path.join(DATA_DIR, "strava_credentials.json")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"
OAUTH_PORT = 8765


def load_credentials():
    """Return (client_id, client_secret, refresh_token) from disk, or
    ("", "", "") if none/unreadable."""
    if not os.path.exists(CREDENTIALS_PATH):
        return "", "", ""
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        client_id = data.get("client_id", "")
        client_secret = data.get("client_secret", "")
        enc = data.get("refresh_token_enc", "")
        refresh_token = dpapi_decrypt(base64.b64decode(enc)).decode("utf-8") if enc else ""
        return client_id, client_secret, refresh_token
    except Exception:
        return "", "", ""


def save_credentials(client_id, client_secret, refresh_token=""):
    enc = base64.b64encode(dpapi_encrypt(refresh_token.encode("utf-8"))).decode("ascii") if refresh_token else ""
    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"client_id": client_id, "client_secret": client_secret, "refresh_token_enc": enc}, fh)
    secure_credential_file(CREDENTIALS_PATH)


def clear_credentials():
    if os.path.exists(CREDENTIALS_PATH):
        os.remove(CREDENTIALS_PATH)


class StravaImportWindow(ImportWindowBase):
    PROVIDER = "Strava"
    PROVIDER_KEY = "strava"
    TYPE_CHOICES = {"All": "all"}

    def _headless_init(self):
        client_id, client_secret, refresh_token = load_credentials()
        if not client_id or not client_secret or not refresh_token:
            return False  # browser OAuth required; can't run headlessly without a saved token
        from activity_import_base import _FakeVar
        self.client_id_var     = _FakeVar(client_id)
        self.client_secret_var = _FakeVar(client_secret)
        self._refresh_token    = refresh_token
        return True

    def build_auth_frame(self):
        cred = ttk.LabelFrame(self, text="Strava account")
        cred.pack(fill="x", padx=10, pady=8)

        ttk.Label(
            cred,
            text="Requires a free Strava API application (Client ID + Client Secret) from "
                 "strava.com/settings/api, with \"Authorization Callback Domain\" set to localhost.",
            wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=5, padx=4, pady=(4, 8), sticky="w")

        ttk.Label(cred, text="Client ID:").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self.client_id_var = tk.StringVar()
        ttk.Entry(cred, textvariable=self.client_id_var, width=16).grid(row=1, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(cred, text="Client Secret:").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self.client_secret_var = tk.StringVar()
        self.secret_entry = ttk.Entry(cred, textvariable=self.client_secret_var, width=30, show="*")
        self.secret_entry.grid(row=1, column=3, padx=4, pady=4, sticky="w")

        self.show_secret_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cred, text="Show", variable=self.show_secret_var, command=self._toggle_secret).grid(
            row=1, column=4, padx=4, pady=4
        )

        self.remember_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cred, text="Remember (Client Secret stored as entered; token encrypted)",
                        variable=self.remember_var).grid(row=2, column=1, columnspan=3, padx=4, pady=4, sticky="w")

        self.login_btn = ttk.Button(cred, text="Connect to Strava && fetch activities", command=self.login_and_fetch)
        self.login_btn.grid(row=2, column=4, padx=4, pady=4, sticky="e")
        ttk.Button(cred, text="Forget saved credentials", command=self.forget_credentials).grid(
            row=3, column=4, padx=4, pady=4, sticky="e"
        )

        client_id, client_secret, refresh_token = load_credentials()
        if client_id:
            self.client_id_var.set(client_id)
        if client_secret:
            self.client_secret_var.set(client_secret)
        self._refresh_token = refresh_token

    def _toggle_secret(self):
        self.secret_entry.config(show="" if self.show_secret_var.get() else "*")

    def forget_credentials(self):
        clear_credentials()
        self._refresh_token = ""
        messagebox.showinfo("Strava import", "Saved Strava credentials removed.")

    # --------------------------------------------------------------- provider hooks
    def pre_login_check(self):
        client_id = self.client_id_var.get().strip()
        client_secret = self.client_secret_var.get().strip()
        if not client_id or not client_secret:
            messagebox.showerror(
                "Strava import",
                "Please enter the Client ID and Client Secret from your Strava API "
                "application (strava.com/settings/api)."
            )
            return False
        return True

    def authenticate(self):
        client_id = self.client_id_var.get().strip()
        client_secret = self.client_secret_var.get().strip()

        tokens = None
        if self._refresh_token:
            try:
                tokens = self._token_request({
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                })
            except Exception as e:
                self._log(f"Saved Strava session expired, re-authorizing in browser ({e})...")
                tokens = None

        if tokens is None:
            self._log("Opening your browser to authorize Trip Manager with Strava...")
            code, redirect_uri = run_oauth_flow(
                lambda redirect_uri: self._authorize_url(client_id, redirect_uri),
                port=OAUTH_PORT,
            )
            tokens = self._token_request({
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
            })

        self.access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token", self._refresh_token)
        if self.remember_var.get():
            save_credentials(client_id, client_secret, self._refresh_token)
        else:
            clear_credentials()

    @staticmethod
    def _authorize_url(client_id, redirect_uri):
        from urllib.parse import urlencode
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": "activity:read_all",
        }
        return AUTHORIZE_URL + "?" + urlencode(params)

    @staticmethod
    def _token_request(data):
        resp = requests.post(TOKEN_URL, data=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def fetch_tours(self):
        tours = {}
        headers = {"Authorization": f"Bearer {self.access_token}"}
        page = 1
        while True:
            resp = requests.get(
                f"{API_BASE}/athlete/activities",
                headers=headers, params={"per_page": 200, "page": page}, timeout=30,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for act in batch:
                tours[act["id"]] = {
                    "date": act.get("start_date_local", ""),
                    "name": act.get("name", ""),
                    "sport": act.get("sport_type") or act.get("type", ""),
                    "distance": act.get("distance", 0),
                    "type": act.get("type", ""),
                }
            if len(batch) < 200:
                break
            page += 1
        return tours

    def download_activity(self, tid, tour_meta):
        headers = {"Authorization": f"Bearer {self.access_token}"}
        sel = self.selected_extras
        stream_keys = ["latlng", "altitude", "time"]
        if sel.get('hr'):  stream_keys.append("heartrate")
        if sel.get('cad'): stream_keys.append("cadence")
        if sel.get('pwr'): stream_keys.append("watts")
        if sel.get('tmp'): stream_keys.append("temp")

        resp = requests.get(
            f"{API_BASE}/activities/{tid}/streams",
            headers=headers,
            params={"keys": ",".join(stream_keys), "key_by_type": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        streams = resp.json()

        latlng = streams.get("latlng", {}).get("data") or []
        if not latlng:
            raise RuntimeError("Strava has no GPS track for this activity.")
        altitude     = streams.get("altitude",  {}).get("data") or []
        time_offsets = streams.get("time",      {}).get("data") or []
        hr_data      = streams.get("heartrate", {}).get("data") or []
        cad_data     = streams.get("cadence",   {}).get("data") or []
        pwr_data     = streams.get("watts",     {}).get("data") or []
        tmp_data     = streams.get("temp",      {}).get("data") or []

        date_str = tour_meta.get("date", "")
        if date_str.endswith("Z"):
            date_str = date_str[:-1]
        start_time = datetime.fromisoformat(date_str) if date_str else datetime(1970, 1, 1)

        points = []
        for i, (lat, lon) in enumerate(latlng):
            ele = altitude[i] if i < len(altitude) else None
            offset = time_offsets[i] if i < len(time_offsets) else 0
            extras = {}
            if i < len(hr_data)  and hr_data[i]  is not None: extras['hr']  = hr_data[i]
            if i < len(cad_data) and cad_data[i] is not None: extras['cad'] = cad_data[i]
            if i < len(pwr_data) and pwr_data[i] is not None: extras['pwr'] = pwr_data[i]
            if i < len(tmp_data) and tmp_data[i] is not None: extras['tmp'] = tmp_data[i]
            points.append((lat, lon, ele, offset, extras or None))

        return build_gpx(tour_meta.get("name") or str(tid), points, start_time)
