FROM python:3.11-slim

WORKDIR /app

# Install runtime deps only
COPY requirements.prod.txt .
RUN pip install --no-cache-dir -r requirements.prod.txt

COPY server.py database.py telc_seeds.py ./

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
