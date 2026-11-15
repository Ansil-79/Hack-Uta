# Olympus 21 — VS Code project

This project is your original HackUTA 2026 Olympus 21 frontend, reorganized without changing its design or gameplay.

## Files
- `index.html` — markup and references to the other files
- `style.css` — all CSS, including responsive layout and cards
- `script.js` — storm animation, interactions, and Blackjack logic

## How to run
1. Download and unzip `olympus21-vscode.zip`.
2. In VS Code, choose **File > Open Folder** and select the `olympus21-vscode` folder.
3. Open `index.html`.
4. For a quick preview, open `index.html` in Chrome or use the VS Code **Live Server** extension and choose **Open with Live Server**.
5. Click **Explore without a card** to start Blackjack without NFC.

## Important NFC note
This is a frontend-only package. The built-in Web NFC code requires a supported device/browser, HTTPS, and user permission. It is **not yet wired to `nfc_server.py`** or a USB NFC reader/WebSocket server. On a desktop browser without Web NFC, the NFC button starts demo mode. The `Explore without a card` link also starts demo mode.

Google Fonts are loaded from the web, so your browser will need internet access for the exact original typography.
