FROM python:3.12-slim

RUN apt-get update && apt-get install -y smartmontools && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python3", "monitor.py"]
