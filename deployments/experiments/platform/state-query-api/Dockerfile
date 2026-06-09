FROM python:3.11-slim

RUN pip install --no-cache-dir fastapi uvicorn redis

COPY main.py /app/main.py
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
