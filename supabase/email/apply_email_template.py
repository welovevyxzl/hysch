"""Lägger in mejlmallen och kodinställningarna i Supabase. Kräver en egen e-postleverantör (custom SMTP) i projektet:
Supabase släpper inte igenom egna mejlmallar på den inbyggda avsändaren. Kör: python apply_email_template.py"""
import io, json, re, sys, urllib.request, urllib.error
ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
env = {}
for line in io.open(ROOT + r"\..\.env", encoding="utf-8-sig"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, _, v = line.rstrip("\n").partition("="); env[k.strip()] = v
ref = re.match(r"https://([a-z0-9]+)\.supabase\.co", env["SUPABASE_URL"]).group(1)
html = io.open(ROOT + r"\templates\code.html", encoding="utf-8").read()
body = {"mailer_templates_magic_link_content": html, "mailer_templates_confirmation_content": html,
        "mailer_subjects_magic_link": "Din Hysch-kod", "mailer_subjects_confirmation": "Din Hysch-kod",
        "mailer_otp_length": 6, "mailer_otp_exp": 600}
req = urllib.request.Request(f"https://api.supabase.com/v1/projects/{ref}/config/auth", data=json.dumps(body).encode(), method="PATCH",
                             headers={"authorization": "Bearer " + env["SUPABASE_ACCESS_TOKEN"], "content-type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=30); print("Mejlmallen är installerad.")
except urllib.error.HTTPError as e:
    sys.exit("Misslyckades: " + e.read().decode()[:300])
