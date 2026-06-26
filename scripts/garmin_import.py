"""Garmin Connect import GUI: fetch new activities from a Garmin Connect
account and download them as GPX files into the trip library (SOURCE_DIR).

Uses the unofficial "garminconnect" package (email/password login, same
approach as the official Garmin Connect website/app -- there is no public
Garmin API for personal accounts)."""

import os
import json
import base64

from trip_manager import DATA_DIR
from dpapi_utils import dpapi_encrypt, dpapi_decrypt, secure_credential_file
from activity_import_base import ImportWindowBase

import tkinter as tk
from tkinter import ttk, messagebox

CREDENTIALS_PATH = os.path.join(DATA_DIR, "garmin_credentials.json")

ACTIVITIES_PAGE_SIZE = 100
MAX_ACTIVITIES = 5000


def load_credentials():
    """Return (email, password) from disk, or ("", "") if none/unreadable."""
    if not os.path.exists(CREDENTIALS_PATH):
        return "", ""
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        email = data.get("email", "")
        enc = data.get("password_enc", "")
        if not enc:
            return email, ""
        password = dpapi_decrypt(base64.b64decode(enc)).decode("utf-8")
        return email, password
    except Exception:
        return "", ""


def save_credentials(email, password):
    enc = base64.b64encode(dpapi_encrypt(password.encode("utf-8"))).decode("ascii")
    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as fh:
        json.dump({"email": email, "password_enc": enc}, fh)
    secure_credential_file(CREDENTIALS_PATH)


def clear_credentials():
    if os.path.exists(CREDENTIALS_PATH):
        os.remove(CREDENTIALS_PATH)


class GarminImportWindow(ImportWindowBase):
    PROVIDER = "Garmin Connect"
    PROVIDER_KEY = "garmin"
    TYPE_CHOICES = {"All": "all"}

    def _headless_init(self):
        email, password = load_credentials()
        if not email or not password:
            return False
        from activity_import_base import _FakeVar
        self.email_var    = _FakeVar(email)
        self.password_var = _FakeVar(password)
        return True

    def build_auth_frame(self):
        cred = ttk.LabelFrame(self, text="Garmin Connect account")
        cred.pack(fill="x", padx=10, pady=8)

        ttk.Label(cred, text="Email:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.email_var = tk.StringVar()
        ttk.Entry(cred, textvariable=self.email_var, width=30).grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(cred, text="Password:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.password_var = tk.StringVar()
        self.password_entry = ttk.Entry(cred, textvariable=self.password_var, width=22, show="*")
        self.password_entry.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        self.show_pwd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cred, text="Show", variable=self.show_pwd_var, command=self._toggle_password).grid(
            row=0, column=4, padx=4, pady=4
        )

        self.remember_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cred, text="Remember password (encrypted)", variable=self.remember_var).grid(
            row=1, column=1, padx=4, pady=4, sticky="w"
        )

        self.login_btn = ttk.Button(cred, text="Login && fetch activities", command=self.login_and_fetch)
        self.login_btn.grid(row=1, column=3, padx=4, pady=4, sticky="e")
        ttk.Button(cred, text="Forget saved password", command=self.forget_password).grid(
            row=1, column=4, padx=4, pady=4
        )

        ttk.Label(
            cred,
            text="If your account uses two-factor authentication, login may fail; "
                 "disable 2FA for Garmin Connect or use the Komoot/Strava import instead.",
            wraplength=760, justify="left", foreground="#777",
        ).grid(row=2, column=0, columnspan=5, padx=4, pady=(0, 4), sticky="w")

        saved_email, saved_password = load_credentials()
        if saved_email:
            self.email_var.set(saved_email)
        if saved_password:
            self.password_var.set(saved_password)

    def _toggle_password(self):
        self.password_entry.config(show="" if self.show_pwd_var.get() else "*")

    def forget_password(self):
        clear_credentials()
        self.password_var.set("")
        messagebox.showinfo("Garmin import", "Saved password removed.")

    # --------------------------------------------------------------- provider hooks
    def pre_login_check(self):
        email = self.email_var.get().strip()
        password = self.password_var.get()
        if not email or not password:
            messagebox.showerror("Garmin import", "Please enter both an email and a password.")
            return False

        if self.remember_var.get():
            try:
                save_credentials(email, password)
            except Exception as e:
                self._log(f"Could not save credentials: {e}")
        else:
            clear_credentials()
        return True

    def authenticate(self):
        from garminconnect import Garmin

        email = self.email_var.get().strip()
        password = self.password_var.get()
        client = Garmin(email, password)
        client.login()
        self.client = client

    def fetch_tours(self):
        tours = {}
        start = 0
        while start < MAX_ACTIVITIES:
            batch = self.client.get_activities(start, ACTIVITIES_PAGE_SIZE)
            if not batch:
                break
            for act in batch:
                activity_type = (act.get("activityType") or {}).get("typeKey", "")
                tours[act["activityId"]] = {
                    "date": (act.get("startTimeLocal") or "").replace(" ", "T"),
                    "name": act.get("activityName", ""),
                    "sport": activity_type,
                    "distance": act.get("distance") or 0,
                    "type": activity_type,
                }
            if len(batch) < ACTIVITIES_PAGE_SIZE:
                break
            start += ACTIVITIES_PAGE_SIZE
        return tours

    def download_activity(self, tid, tour_meta):
        from garminconnect import Garmin

        data = self.client.download_activity(tid, dl_fmt=Garmin.ActivityDownloadFormat.GPX)
        return data.decode("utf-8") if isinstance(data, bytes) else data
