"""Kopplar Resend till Supabase Auth och installerar Hysch-mejlet. Kör när RESEND_API_KEY och MAIL_FROM_EMAIL är ifyllda i .env:
    python connect_resend.py
Skriver aldrig ut nycklar."""
import io, json, os, re, sys, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
env = {}
for line in io.open(os.path.join(HERE, "..", "..", ".env"), encoding="utf-8-sig"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, _, v = line.rstrip("\n").partition("="); env[k.strip()] = v
for need in ("SUPABASE_URL", "SUPABASE_ACCESS_TOKEN", "RESEND_API_KEY", "MAIL_FROM_EMAIL"):
    if not env.get(need):
        sys.exit(f"Fyll i {need} i .env först.")
ref = re.match(r"https://([a-z0-9]+)\.supabase\.co", env["SUPABASE_URL"]).group(1)
html = io.open(os.path.join(HERE, "..", "templates", "code.html"), encoding="utf-8").read()

body = {
    "external_email_enabled": True,
    "smtp_host": "smtp.resend.com", "smtp_port": "465", "smtp_user": "resend", "smtp_pass": env["RESEND_API_KEY"],
    "smtp_admin_email": env["MAIL_FROM_EMAIL"], "smtp_sender_name": env.get("MAIL_FROM_NAME") or "Hysch",
    "rate_limit_email_sent": 60,                      # per timme, så en lansering inte fastnar på två mejl
    "mailer_otp_length": 6, "mailer_otp_exp": 600,
    "mailer_subjects_magic_link": "Din Hysch-kod", "mailer_subjects_confirmation": "Din Hysch-kod",
    "mailer_templates_magic_link_content": html, "mailer_templates_confirmation_content": html,
}
req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{ref}/config/auth", data=json.dumps(body).encode("utf-8"), method="PATCH",
                             headers={"authorization": "Bearer " + env["SUPABASE_ACCESS_TOKEN"], "content-type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=40)
except urllib.error.HTTPError as e:
    msg = e.read().decode()[:400].replace(env["RESEND_API_KEY"], "***")
    sys.exit("Supabase nekade: " + msg)
print("Klart: Resend är kopplat och Hysch-mejlet är installerat.")
