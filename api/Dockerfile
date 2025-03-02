FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8006

#CMD ["python", "-c", "import uvicorn; uvicorn.run('app:app', host='0.0.0.0', port=8006)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8006"]