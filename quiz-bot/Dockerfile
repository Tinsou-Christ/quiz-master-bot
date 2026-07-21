FROM python:3.11-slim
WORKDIR /usr/src/bot

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Dossier persistant pour les scores (monter un volume ici)
VOLUME ["/data"]
ENV DATA_DIR=/data

CMD ["python", "main.py"]
