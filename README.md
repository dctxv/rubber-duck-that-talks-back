# Rubber duck that talks back

An education workspace where students write out their working by hand and each completed line is transcribed into LaTeX in the background.

## Run locally

Requires Node.js 20 or newer.

1. Install dependencies:

   ```sh
   npm install
   ```

2. Copy `.env.example` to `.env` and set your OpenRouter API key:

   ```dotenv
   OPENROUTER_API_KEY=your_openrouter_api_key_here
   ```

3. Create a Python environment and install the verifier dependencies:

   ```sh
   python -m venv .venv
   # Windows: .venv\Scripts\python -m pip install -r verifier\requirements.txt
   # macOS/Linux: .venv/bin/python -m pip install -r verifier/requirements.txt
   ```

4. Start the verifier in one terminal:

   ```sh
   # Windows
   .venv\Scripts\python verifier\app.py

   # macOS/Linux
   .venv/bin/python verifier/app.py
   ```

5. Start the Node app in a second terminal:

   ```sh
   npm start
   ```

6. Open [http://localhost:3000](http://localhost:3000).

Write one maths step per line. A line transcribes after two seconds of idle time or when you begin writing on another line. Tap its typeset transcription to edit the raw LaTeX; press Enter or tap away to render it again.

The OpenRouter API key is read only by `server.js` and never sent to the browser. Raw model output is logged in the server console for debugging. The default model is `openai/gpt-5.6-luna`; set `OPENROUTER_MODEL` in `.env` to use another vision-capable OpenRouter model.

The verifier listens on port `8000` by default and reads `PORT` when deployed. The Node server reaches it through `VERIFIER_URL`, which defaults to `http://localhost:8000`. Both services log every verdict with `prev`, `current`, and `valid`.

## DigitalOcean App Platform

Deploy this repository as two service components:

- **Node web service:** repository root, build command `npm install`, run command `npm start`, with the public HTTP route.
- **Python internal service:** source directory `verifier`, build command `pip install -r requirements.txt`, run command `python app.py`, with its listening port configured as an internal port and no public route.

Name the Python component `verifier`, then set the Node component's runtime variable `VERIFIER_URL` to `${verifier.PRIVATE_URL}`. The browser continues to call only the public Node service.
