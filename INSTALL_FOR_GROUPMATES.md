# Installing MORVIX on your own computer

MORVIX runs entirely on your computer: you get your own server, dashboard and extension, and your own parent account. Nothing is shared with the computer you got it from.

**You need:** Windows 10 or 11, Google Chrome (or Microsoft Edge), an internet connection for the setup, about 6 GB of free disk space, and 15–40 minutes.

## 1. Install Python 3.12 (skip if you have it)

1. Download Python 3.12 from https://www.python.org/downloads/windows/ ("Windows installer (64-bit)").
2. In the installer, **tick "Add python.exe to PATH"**, then click **Install Now**.

## 2. Unzip MORVIX

1. Right-click `MORVIX_V3_share.zip` → **Extract All…**, and extract it somewhere permanent, for example `Documents`.
2. Don't run it from inside the ZIP, and don't move the folder later. If you do move it, run `launcher\install_shortcuts.ps1` again.

## 3. Run the setup

1. Open the `MORVIX_V3` folder and double-click **`SETUP MORVIX.bat`**.
   - If Windows shows "Windows protected your PC", click **More info → Run anyway**.
2. The setup does the following:
   - finds Python;
   - creates MORVIX's own Python environment;
   - installs the packages;
   - downloads the AI models (about 2.7 GB; skipped if the ZIP already contains the `models` folder);
   - creates a **MORVIX** shortcut on your Desktop.
3. When it asks **"Set up the sending Gmail now?"**, you need a Gmail account that will send the sign-up codes:
   1. Turn on 2-Step Verification for that Gmail account.
   2. Create an App Password at https://myaccount.google.com/apppasswords.
   3. Type the Gmail address, then paste the 16-letter App Password (nothing shows while you paste).
   4. Wait for **"Test email sent"**.
   - Never share your App Password with anyone, including in group chats.

## 4. Add the extension to Chrome

At the end, the setup opens `chrome://extensions` and the extension folder, and copies the folder path for you.

1. Turn on **Developer mode** (top right).
2. Click **Load unpacked**, paste the path into the folder window's address bar (Ctrl+V, Enter), and click **Select Folder**.
3. Pin MORVIX with the puzzle-piece icon in the toolbar.

## 5. Create your account and pair

1. Double-click **MORVIX** on the Desktop. The dashboard opens in its own window.
2. **Create account**, then enter the 6-digit code from your email.
3. On the Dashboard, click **Generate pairing code**. Paste the code in the MORVIX extension popup and click **Pair extension**.
4. Test it on http://127.0.0.1:8000/demo/ in a normal Chrome tab.

## Everyday use and problems

| What | How |
|---|---|
| Start MORVIX | Double-click **MORVIX** on the Desktop. The extension only works while MORVIX is running. |
| Stop MORVIX | Start menu → MORVIX → **Stop MORVIX** |
| Change the sending Gmail | Start menu → MORVIX → **MORVIX Email Setup** |
| "Email is not set up" on Create account | Run MORVIX Email Setup, then reopen MORVIX. |
| "MORVIX could not start" | Run `SETUP MORVIX.bat` again; it repairs what is missing. The error log is `data\server-error.log`. |
| Extension says "Backend offline" | Start MORVIX from the Desktop shortcut. |
| Extension shows nothing at all | In `chrome://extensions`, make sure MORVIX is switched on, then click its reload ↻ icon. |
