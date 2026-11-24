FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup -S -g 10001 messenger \
    && adduser -S -u 10001 -G messenger messenger

WORKDIR /service
COPY requirements.txt .
RUN pip install --requirement requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY messenger ./messenger
COPY entrypoint.sh .
RUN chmod 0555 /service/entrypoint.sh \
    && chown -R messenger:messenger /service \
    && mkdir -p /attachments/clean /attachments/quarantine \
    && chown -R messenger:messenger /attachments

USER messenger
EXPOSE 8000

ENTRYPOINT ["/service/entrypoint.sh"]
