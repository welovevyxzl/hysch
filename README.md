<p align="center">
  <img src="docs/hero.png" alt="Viska: know what to say next" width="100%">
</p>

<p align="center">
  <a href="https://github.com/welovevyxzl/viska/releases/latest/download/Viska.exe"><img src="docs/download.png" alt="Download for Windows" width="300"></a>
</p>

<p align="center">
  <a href="#during-the-call">During the call</a> &nbsp;·&nbsp;
  <a href="#after-the-call">After the call</a> &nbsp;·&nbsp;
  <a href="#how-it-works">How it works</a> &nbsp;·&nbsp;
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="#privacy">Privacy</a> &nbsp;·&nbsp;
  <a href="#build-from-source">Build from source</a>
</p>

<br>

Viska is a Windows app for people who sell over the phone. It listens to your call, shows you what to say next while you are still talking, and writes up the whole call when you hang up: a summary, what you need to do, and how you did.

It is built for calls in Swedish.

<br>

## During the call

<table>
<tr>
<td width="55%" valign="middle">

A small window sits on top of your call. The other person says "we have no budget for this", and a moment later you see a line you can say back, plus two alternatives.

Before you start, you fill in who you are calling, who you are, what you sell, the price, your best arguments and the objections you expect. Viska only uses what you wrote. It will not invent prices or promises, and if someone asks who is calling, it answers with your name and company.

Press **N** for a new suggestion, **P** to pause, **1** or **2** to swap in an alternative.

</td>
<td width="45%" align="center" valign="middle">
<img src="docs/live.png" alt="The live view" width="360">
</td>
</tr>
</table>

<br>

## After the call

When you press Avsluta, Viska reads the whole call and gives you a debrief.

<p align="center">
  <img src="docs/show-debrief.png" alt="The debrief screen and the list of past calls" width="100%">
</p>

- A **score** from 0 to 100, the outcome (sold, meeting booked, follow-up, no), the mood, and how much each of you talked.
- The **next step** that matters most, and a list of **to-dos** for you and for them. You can tick them off.
- What **they said** and what **you said**, what went well, and what to do better next time.
- Every **objection**, how you handled it, and a better reply for next time.
- A **follow-up email**, ready to copy.
- The **full transcript** with timestamps.

Every call is saved, so you can open old ones from **Tidigare samtal** on the start screen.

<br>

## How it works

<p align="center">
  <img src="docs/flow.png" alt="Listen, think, say" width="100%">
</p>

Each audio source is cut into turns by a simple voice detector that tracks the background noise, so quiet calls still register. When someone stops talking, the whole turn goes to OpenAI's `gpt-4o-transcribe` in one piece, not as a live stream, so the model hears the full sentence and gets it right more often. It falls back to `whisper-1` if your account does not have it. Names and product words from your form go along as spelling hints.

The coach is `gpt-6-luna`, and the debrief is written by `gpt-6.1-terra`. Your API key needs access to these models.

<br>

## Install

1. Download [Viska.exe](https://github.com/welovevyxzl/viska/releases/latest/download/Viska.exe). There is no installer.
2. Run it and paste your OpenAI API key. It is saved on your PC only.
3. Fill in the form, choose a mode and press **Starta samtal**.

Windows SmartScreen will probably warn about an unknown publisher, because the exe is not code-signed. Choose More info, then Run anyway.

You pay OpenAI for what you use.

### Two modes

| | Computer | Phone on speaker |
|---|---|---|
| For | Calls on your PC: Teams, Meet, WhatsApp | A phone placed next to the PC |
| Their voice | Captured from the PC's audio output | Picked up by the microphone |
| Who is talking | Detected automatically | Hold the button (or Space) while they talk |

Computer mode gives the best transcription, because their voice arrives digitally. In phone mode it has to travel through the speaker and the air.

<br>

## Privacy

- **On your PC:** your settings and API key, and every call's transcript and debrief, in `%APPDATA%\Viska`. Viska does not keep audio.
- **Sent to OpenAI, using your own key:** the audio of each turn, for transcription, and the text of the conversation, for the coach and the debrief.
- **Tell the person you are calling.** Say at the start that the call is transcribed with AI and saved. GDPR requires it, and Viska reminds you in the live view.

<br>

## Build from source

Needs Windows and a recent Python 3. The release exe was built with 3.14.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Put your key in `.env` (git-ignored), then run:

```bash
.venv\Scripts\python samtalscoach.py
```

To build the exe:

```bash
.venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name Viska --add-data "ui;ui" samtalscoach.py
```

The result is `dist\Viska.exe`.

<details>
<summary>Files and troubleshooting</summary>

<br>

- `samtalscoach.py` holds audio capture, transcription, the coach and the debrief. `ui/index.html` is the whole interface, shown with pywebview (WebView2).
- The key is read from the `OPENAI_API_KEY` environment variable, then a `.env` file next to the exe, then the saved settings.
- Settings are in `%APPDATA%\Viska\config.json`, saved calls in `%APPDATA%\Viska\calls`, and a debug log in `%APPDATA%\Viska\viska.log`. The log shows how long each transcription took.
- If the mic will not open, it is probably muted (Fn key or Windows sound settings). Unmute, press Avsluta, then Starta.
- If you hear nothing from the other person in Computer mode, check that the call audio plays through the output device chosen in setup.
- If the debrief shows an error, it says what went wrong, and there is a button to try again. The transcript is saved either way.

</details>
