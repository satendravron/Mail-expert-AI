# 🚀 Mail Expert AI — Production Deployment & Publishing Guide

This guide details how to deploy the **Mail Expert AI** backend server to free cloud hosting platforms (Render, Railway, Docker, Fly.io) and how to package the Chrome Extension for publication.

---

## 🐋 Option 1: Docker Container Deployment (Local or Cloud)

### 1. Build Docker Image
```bash
docker build -t mail-expert-ai .
```

### 2. Run Docker Container
```bash
docker run -d -p 8000:8000 -v mail_data:/app/data --name mail-expert-ai-app mail-expert-ai
```
Access the dashboard at `http://localhost:8000/`.

---

## ☁️ Option 2: Deploy Free on Render.com (1-Click)

1. Push your repository code to GitHub.
2. Sign in to [Render.com](https://render.com/).
3. Click **New +** $\rightarrow$ **Web Service**.
4. Connect your GitHub repository.
5. Select **Docker** as the Environment.
6. Click **Deploy Web Service**. Render will build the `Dockerfile` automatically and provide a free live HTTPS URL (e.g. `https://mail-expert-ai.onrender.com`).

---

## 🚂 Option 3: Deploy on Railway.app

1. Sign in to [Railway.app](https://railway.app/).
2. Click **New Project** $\rightarrow$ **Deploy from GitHub repo**.
3. Railway will detect `Dockerfile` automatically.
4. Go to **Settings** $\rightarrow$ **Networking** $\rightarrow$ Click **Generate Domain**.

---

## 🧩 Option 4: Publish Chrome Extension

1. The Chrome Extension package is pre-built in the repository as [`mail-expert-extension-v1.2.zip`](mail-expert-extension-v1.2.zip).
   To manually re-package, run:
   ```bash
   python -c "import zipfile, os; files=['manifest.json', 'popup.html', 'popup.js', 'content_script.js', 'content_style.css', 'icon48.png', 'icon192.png', 'icon512.png']; z=zipfile.ZipFile('mail-expert-extension-v1.2.zip', 'w'); [z.write(f) for f in files if os.path.exists(f)]; z.close()"
   ```
2. Go to the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole).
3. Click **Add new item** and upload `mail-expert-extension-v1.2.zip`.
4. Fill in extension store listing details, screenshots, and submit for review.

---

## 🔑 Environment Variables Reference

When deploying to production, optionally configure:
- `GEMINI_API_KEY`: API Key for Google Gemini LLM summarization.
- `OPENAI_API_KEY`: API Key for OpenAI GPT summarization.
- `PORT`: Server binding port (defaults to `8000`).
