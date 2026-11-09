# Viska

Windows-app som lyssnar på ett säljsamtal och visar vad du kan säga härnäst, i realtid.

- **Tal till text:** OpenAI `gpt-transcribe` (Realtime API, cirka $0,0045/min)
- **Svarsförslag:** OpenAI `gpt-6-luna`
- **Gränssnitt:** HTML/CSS i `ui/index.html`, visat med pywebview (WebView2)

## Två lägen

- **Dator:** samtalet går via datorn (Teams, Meet, WhatsApp). Motparten fångas från datorns ljud, du från mikrofonen.
- **Telefon på högtalare:** allt hörs i mikrofonen. Håll in knappen (eller mellanslag) medan motparten pratar.

Påminn den du ringer om att samtalet transkriberas med AI (GDPR).

## Kom igång

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Lägg in din nyckel i `.env`, kör sedan:

```bash
.venv\Scripts\python samtalscoach.py
```

## Bygg .exe

```bash
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name Viska --add-data "ui;ui" samtalscoach.py
```

Resultatet hamnar i `dist\Viska.exe`. Formuläret sparas i `%APPDATA%\Viska\config.json` och en felsökningslogg i `%APPDATA%\Viska\viska.log`.
