FROM python:3.12-slim

WORKDIR /app

# git is required for Milestone 3 external anchoring of Merkle checkpoints
# (checkpoints.py shells out to it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/keys /app/checkpoint-repo

EXPOSE 8420

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8420"]
