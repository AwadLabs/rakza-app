FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

# الخادم يقدّم الـ PWA والـ API معًا من نفس العملية
CMD ["sh", "-c", "uvicorn api.server:app --host 0.0.0.0 --port ${PORT}"]
