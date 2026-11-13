# A hosted demo of the recruiter page. Standard library only, so the image is just Python and this repository.
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "python3 -m shortlist serve --host 0.0.0.0 --port ${PORT:-8080}"]
