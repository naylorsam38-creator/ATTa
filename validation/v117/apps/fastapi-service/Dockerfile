# Offline install from vendored wheels.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY wheels ./wheels
RUN pip install --no-cache-dir --no-index --find-links ./wheels -r requirements.txt
COPY main.py ./
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
