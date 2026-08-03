FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Downloads the Silero VAD / turn-detector weights at build time so the first
# caller doesn't pay for it mid-call.
RUN python main.py download-files || true

# `start` is the production worker command; `dev` is for local iteration.
CMD ["python", "main.py", "start"]
